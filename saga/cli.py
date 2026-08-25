from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import platform
import unicodedata
import shutil
import subprocess
import sys

from . import __version__
from .api import SagaSession, compile_file, compile_source, parse_source, run_file, run_source
from .errors import InternalLanguageError, SourceError, format_diagnostic
from .diagnostics import all_specs, get_spec, localize_message, normalize_language
from .formatter import format_source
from .linter import lint_program
from .metadata import extract_metadata
from .native import Capabilities
from .project import find_project, saga_files
from .source_units import read_source_file
from .project_templates import TEMPLATES
from .stdlib import MODULES
from .standards import PROPOSER_TYPES, StandardsError, StandardsRegistry
from .package import PackageError, build_lock, pack_project, verify_lock
from .exitcodes import CONFORMANCE_FAILURE, INPUT_ERROR, INTERNAL_ERROR, for_error
from .limits import RESOURCE_MODEL

VERSION = __version__



def _diagnostic_source(error: SourceError, fallback: str = "") -> str:
    if error.filename not in {"<input>", "<repl>", "<session>"}:
        try:
            return pathlib.Path(error.filename).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            pass
    return fallback


def _diagnostic_payload(error: SourceError, source: str = "", language: str = "auto") -> dict:
    language = normalize_language(language)
    spec = get_spec(error.diagnostic_id)
    if error.hint and error.hint.startswith("candidate:"):
        candidate = error.hint.split(":", 1)[1]
        help_text = f"Did you mean `{candidate}`?" if language == "en" else f"`{candidate}` の間違いではありませんか？"
    elif language == "ja":
        help_text = error.hint or (spec.help(language) if spec else None)
    else:
        help_text = (spec.help(language) if spec else None) or error.hint
    return {
        "schema": 2, "language": "Saga", "implementation_version": VERSION,
        "locale": language,
        "diagnostic": {
            # `code` is the stable broad compatibility category retained from 0.7/0.8.
            "code": error.code,
            "id": error.diagnostic_id,
            "severity": "error",
            "title": spec.title(language) if spec else error.message,
            "message": localize_message(error.code, error.diagnostic_id, error.message, language, error.detail_data),
            "raw_message": error.message,
            "filename": error.filename,
            "range": {
                "start": {"line": error.line, "column": error.column},
                "end": {"line": error.line, "column": error.end_column or error.column + 1},
            },
            "help": help_text,
            "explanation": spec.explanation(language) if spec else None,
        },
    }


def _render_error(error: SourceError, source: str = "", mode: str = "text", language: str = "auto") -> str:
    language = normalize_language(language)
    if mode == "json":
        return json.dumps(_diagnostic_payload(error, source, language), ensure_ascii=False, sort_keys=True)
    if mode == "sarif":
        payload = _diagnostic_payload(error, source, language)["diagnostic"]
        result = {
            "version": "2.1.0",
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "runs": [{
                "tool": {"driver": {"name": "Saga", "version": VERSION}},
                "results": [{
                    "ruleId": payload["id"],
                    "level": "error",
                    "message": {"text": payload["title"] + ": " + payload["message"]},
                    "locations": [{"physicalLocation": {
                        "artifactLocation": {"uri": payload["filename"]},
                        "region": {
                            "startLine": payload["range"]["start"]["line"],
                            "startColumn": payload["range"]["start"]["column"],
                            "endLine": payload["range"]["end"]["line"],
                            "endColumn": payload["range"]["end"]["column"],
                        },
                    }}],
                }],
            }],
        }
        return json.dumps(result, ensure_ascii=False, sort_keys=True)
    return format_diagnostic(error, _diagnostic_source(error, source), language=language)

def _read(path: str | pathlib.Path) -> str:
    return read_source_file(path)


def _source_path(value: str) -> pathlib.Path:
    raw = pathlib.Path(value).expanduser()
    if raw.is_symlink():
        raise OSError(f"Sagaソースにシンボリックリンクは使用できません: {raw}")
    target = raw
    if target.is_dir():
        project = find_project(target)
        if project is None:
            raise OSError(f"{target} に saga.toml がありません")
        target = project.entry
    if not target.is_file():
        raise OSError(f"Sagaソースが見つかりません: {target}")
    return target


def _new_project(name: str, template_name: str) -> None:
    root = pathlib.Path(name)
    template = TEMPLATES[template_name]
    root.mkdir(parents=True, exist_ok=False)
    for relative, content in template.files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    if "saga.toml" not in template.files:
        (root / "saga.toml").write_text(
            f'[project]\nname = "{root.name}"\nversion = "0.1.0"\nlanguage = "1.0"\nentry = "main.saga"\ntest_dir = "tests"\ntemplate = "{template_name}"\n',
            encoding="utf-8",
        )
    print(f"Created {root}/ ({template_name}: {template.description})")


def _capabilities(args) -> Capabilities:
    resolve = lambda values: tuple(pathlib.Path(v).expanduser().resolve() for v in (values or []))
    return Capabilities(
        allow_all=bool(args.allow_all),
        read_roots=resolve(args.allow_read),
        write_roots=resolve(args.allow_write),
        net_hosts=tuple(args.allow_net or []),
        db_roots=resolve(args.allow_db),
        allow_ui=bool(args.allow_ui),
        plugin_roots=resolve(args.allow_plugin),
        allow_process=bool(args.allow_process),
        env_names=tuple(args.allow_env or []),
        allow_cloud=bool(args.allow_cloud),
        allow_device=bool(args.allow_device),
    )


def _permission_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--allow-read", action="append", default=[], metavar="PATH", help="読み取りを許可するディレクトリ（複数可）")
    parser.add_argument("--allow-write", action="append", default=[], metavar="PATH", help="書き込みを許可するディレクトリ（複数可）")
    parser.add_argument("--allow-db", action="append", default=[], metavar="PATH", help="DBファイルを許可するディレクトリ（複数可）")
    parser.add_argument("--allow-net", action="append", default=[], metavar="HOST", help="通信を許可するホスト（複数可）")
    parser.add_argument("--allow-ui", action="store_true", help="デスクトップGUIを許可")
    parser.add_argument("--allow-plugin", action="append", default=[], metavar="PATH", help="Pythonプラグインを許可するディレクトリ")
    parser.add_argument("--allow-process", action="store_true", help="外部プロセス起動を許可")
    parser.add_argument("--allow-env", action="append", default=[], metavar="NAME", help="読み取りを許可する環境変数名（複数可）")
    parser.add_argument("--allow-cloud", action="store_true", help="AWS等のクラウドSDK利用を許可")
    parser.add_argument("--allow-device", action="store_true", help="GPIO等の物理デバイス操作を許可")
    parser.add_argument("--allow-all", action="store_true", help="すべての外部権限を許可（開発時のみ）")



def _add_standards_commands(subparsers) -> None:
    standards_p = subparsers.add_parser("standards", help="ISO/IEC標準化準備の証拠レジストリを管理")
    standards_p.add_argument("--root", default=".saga-standards", help="標準化レジストリの保存先")
    actions = standards_p.add_subparsers(dest="standards_action", required=True)

    init_p = actions.add_parser("init", help="標準化レジストリを初期化")
    init_p.add_argument("--project", default="Saga Programming Language")

    actions.add_parser("status", help="標準化準備状況をJSON表示")

    proposer_p = actions.add_parser("set-proposer", help="適格な提案主体と根拠を登録")
    proposer_p.add_argument("--name", required=True)
    proposer_p.add_argument("--type", required=True, choices=sorted(PROPOSER_TYPES))
    proposer_p.add_argument("--country", required=True)
    proposer_p.add_argument("--evidence", required=True)

    leader_p = actions.add_parser("nominate-leader", help="本人同意付きでProject Leader候補を登録")
    for flag in ("name", "email", "organization", "country", "consent"):
        leader_p.add_argument(f"--{flag}", required=True)

    expert_p = actions.add_parser("add-expert", help="本人同意付きで専門家を登録")
    for flag in ("name", "email", "organization", "country", "expertise", "consent"):
        expert_p.add_argument(f"--{flag}", required=True)

    pmember_p = actions.add_parser("add-p-member", help="National Bodyの参加表明を登録")
    pmember_p.add_argument("--national-body", required=True)
    pmember_p.add_argument("--country", required=True)
    pmember_p.add_argument("--expert-email", action="append", required=True)
    pmember_p.add_argument("--evidence", required=True)

    adoption_p = actions.add_parser("add-adoption", help="組織による利用実績を登録")
    adoption_p.add_argument("--organization", required=True)
    adoption_p.add_argument("--country", required=True)
    adoption_p.add_argument("--use-case", required=True)
    adoption_p.add_argument("--evidence", required=True)

    impl_p = actions.add_parser("add-implementation", help="独立実装と適合性レポートを登録")
    impl_p.add_argument("--name", required=True)
    impl_p.add_argument("--language", required=True)
    impl_p.add_argument("--repository", required=True)
    impl_p.add_argument("--report", required=True)
    impl_p.add_argument("--independent-from", default="saga-python")
    impl_p.add_argument("--level", choices=["experimental", "core", "full"], default="experimental",
                        help="適合範囲。core/fullだけが第2実装要件として数えられます")

    lab_p = actions.add_parser("add-lab-report", help="独立機関による試験報告を登録")
    lab_p.add_argument("--organization", required=True)
    lab_p.add_argument("--country", required=True)
    lab_p.add_argument("--scope", required=True)
    lab_p.add_argument("--report", required=True)

    market_p = actions.add_parser("add-market-evidence", help="市場性の証拠を登録")
    market_p.add_argument("--kind", required=True, choices=["survey", "case_study", "procurement", "education", "industry_letter", "usage_metrics", "research"])
    market_p.add_argument("--title", required=True)
    market_p.add_argument("--country", required=True)
    market_p.add_argument("--evidence", required=True)

    actions.add_parser("verify", help="証拠ログのハッシュチェーンを検証")


def _run_standards(args) -> int:
    registry = StandardsRegistry.open(args.root)
    action = args.standards_action
    if action == "init":
        registry.init(args.project); print(f"Initialized: {registry.root}")
    elif action == "status":
        print(json.dumps(registry.status(), ensure_ascii=False, indent=2))
    elif action == "set-proposer":
        registry.set_proposer(name=args.name, proposer_type=args.type, country=args.country, evidence=args.evidence)
    elif action == "nominate-leader":
        registry.nominate_leader(name=args.name, email=args.email, organization=args.organization, country=args.country, consent=args.consent)
    elif action == "add-expert":
        registry.add_expert(name=args.name, email=args.email, organization=args.organization, country=args.country, expertise=args.expertise, consent=args.consent)
    elif action == "add-p-member":
        registry.add_p_member_commitment(national_body=args.national_body, country=args.country, expert_emails=args.expert_email, evidence=args.evidence)
    elif action == "add-adoption":
        registry.add_adoption(organization=args.organization, country=args.country, use_case=args.use_case, evidence=args.evidence)
    elif action == "add-implementation":
        registry.add_implementation(
            name=args.name, language=args.language, repository=args.repository,
            conformance_report=args.report, independent_from=args.independent_from, level=args.level,
        )
    elif action == "add-lab-report":
        registry.add_lab_report(organization=args.organization, country=args.country, scope=args.scope, report=args.report)
    elif action == "add-market-evidence":
        registry.add_market_evidence(kind=args.kind, title=args.title, country=args.country, evidence=args.evidence)
    elif action == "verify":
        chain_ok, detail = registry.verify_event_chain()
        evidence_ok, evidence_errors = registry.verify_evidence()
        print(json.dumps({
            "valid": chain_ok and evidence_ok,
            "event_chain": {"valid": chain_ok, "head_or_error": detail},
            "evidence": {"valid": evidence_ok, "invalid_records": evidence_errors},
        }, ensure_ascii=False, indent=2))
        return 0 if chain_ok and evidence_ok else 1
    if action not in {"status", "verify", "init"}:
        print(f"Recorded: {action}")
    return 0

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="saga",
        description="Machine-control, robotics, and drone programming language with readable control code and explicit hardware authority / 読みやすい制御コードと明示的なハードウェア権限を重視した機械・ロボット・ドローン制御言語",
    )
    parser.add_argument("--version", action="version", version=f"Saga {VERSION}")
    parser.add_argument("--debug", action="store_true", help="内部例外のトレースバックを表示")
    parser.add_argument("--language", default="auto", metavar="LANG", help="診断表示言語（BCP 47。ja/enを内蔵、未対応言語は英語へフォールバック） / diagnostic locale")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="ソースコードを検査して実行")
    run_p.add_argument("file")
    run_p.add_argument("--precision", type=int, default=50, help="Decimalの計算桁数（既定: 50）")
    run_p.add_argument("--step-limit", type=int, default=None, help="任意の実行ステップ予算（未指定なら固定上限なし）")
    run_p.add_argument("--diagnostic-format", choices=["text", "json", "sarif"], default="text")
    run_p.add_argument("--os-sandbox", choices=["off", "strict"], default="off", help="OSレベル隔離。strictはLinux namespaceでネットワーク/PID/IPC/UTSを分離")
    run_p.add_argument("--language", default=argparse.SUPPRESS, metavar="LANG", help="診断表示言語（BCP 47。未対応言語は英語） / diagnostic locale")
    _permission_args(run_p)

    check_p = sub.add_parser("check", help="実行せずに構文・型を検査")
    check_p.add_argument("file")
    check_p.add_argument("--standard", action="store_true", help="標準プロファイルのlintも実行")
    check_p.add_argument("--diagnostic-format", choices=["text", "json", "sarif"], default="text")
    check_p.add_argument("--language", default=argparse.SUPPRESS, metavar="LANG", help="診断表示言語（BCP 47。未対応言語は英語） / diagnostic locale")

    repl_p = sub.add_parser("repl", help="1行ずつ試す簡易REPL")
    repl_p.add_argument("--precision", type=int, default=50)
    repl_p.add_argument("--language", default=argparse.SUPPRESS, metavar="LANG", help="診断表示言語（BCP 47。未対応言語は英語） / diagnostic locale")

    new_p = sub.add_parser("new", help="用途別のSagaプロジェクトを作成")
    new_p.add_argument("name")
    new_p.add_argument("--template", choices=sorted(TEMPLATES), default="basic")

    modules_p = sub.add_parser("modules", help="標準モジュールと関数を表示")
    modules_p.add_argument("module", nargs="?")

    module_p = sub.add_parser("module", help="namespaced moduleのseparate-compilation interfaceを生成")
    module_sub = module_p.add_subparsers(dest="module_action", required=True)
    module_compile = module_sub.add_parser("compile", help="moduleを独立型検査し .smi.json ABI interfaceを生成")
    module_compile.add_argument("file")
    module_compile.add_argument("--output")
    module_compile.add_argument("--root")
    module_verify = module_sub.add_parser("verify", help=".smi.jsonのABI hashとsource freshnessを検証")
    module_verify.add_argument("interface")
    module_verify.add_argument("--source")

    metadata_p = sub.add_parser("metadata", help="型・クラス・アノテーション情報をJSON出力")
    metadata_p.add_argument("file")

    process_p = sub.add_parser("process", help="アノテーションプロセッサを実行")
    process_p.add_argument("file")
    process_p.add_argument("--processor", required=True, help="process(metadata, output_dir) を公開するPythonファイル")
    process_p.add_argument("--output", required=True, help="生成物の出力ディレクトリ")
    process_p.add_argument("--unsafe-processor", action="store_true", help="legacy process(metadata, output_dir) を同一プロセスで実行（非推奨）")

    lint_p = sub.add_parser("lint", help="標準スタイル・公開API・安全境界を検査")
    lint_p.add_argument("path", nargs="?", default=".")
    lint_p.add_argument("--standard", action="store_true", help="標準プロファイル違反をエラーにする")
    lint_p.add_argument("--deny-warnings", action="store_true")

    fmt_p = sub.add_parser("fmt", help="Saga標準レイアウトに整形")
    fmt_p.add_argument("path", nargs="?", default=".")
    fmt_p.add_argument("--check", action="store_true", help="変更せず整形差分があれば失敗")

    migrate_p = sub.add_parser("migrate", help="旧APIのうち安全に変換できる箇所を自然なSaga表記へ移行")
    migrate_p.add_argument("path", nargs="?", default=".")
    migrate_p.add_argument("--write", action="store_true", help="安全に判定できた変換だけファイルへ書き込む")

    test_p = sub.add_parser("test", help="tests配下の.sagaテストを実行")
    test_p.add_argument("path", nargs="?", default=".")
    test_p.add_argument("--precision", type=int, default=50)
    test_p.add_argument("--step-limit", type=int, default=None, help="任意の実行ステップ予算")
    test_p.add_argument("--language", default=argparse.SUPPRESS, metavar="LANG", help="診断表示言語（BCP 47。未対応言語は英語） / diagnostic locale")
    _permission_args(test_p)

    lock_p = sub.add_parser("lock", help="再現可能なプロジェクトlockを生成")
    lock_p.add_argument("path", nargs="?", default=".")

    verify_p = sub.add_parser("verify", help="saga.lockと全ソースの整合性を検証")
    verify_p.add_argument("path", nargs="?", default=".")

    prod_p = sub.add_parser("production-check", help="project/workspaceのproduction gateを実行")
    prod_p.add_argument("path", nargs="?", default=".")
    prod_p.add_argument("--json", dest="report_path", metavar="FILE", help="production reportをJSONへ保存")
    prod_p.add_argument("--native", action="store_true", help="native executableを2回buildしbyte reproducibilityも検証")
    prod_p.add_argument("--machine", action="store_true", help="機械制御GA用のtiming contractとsource-bound safety caseも検証")

    pack_p = sub.add_parser("pack", help="決定的な.sagapkgを作成")
    pack_p.add_argument("path", nargs="?", default=".")
    pack_p.add_argument("--output")

    info_p = sub.add_parser("info", help="言語版・プロファイル・資源モデルを表示")
    info_p.add_argument("--json", action="store_true")

    conformance_p = sub.add_parser("conformance", help="同梱の中核自己適合性試験を実行")
    conformance_p.add_argument("--json", action="store_true")

    explain_p = sub.add_parser("explain", help="診断コードの意味と修正方法を表示 / explain a diagnostic")
    explain_p.add_argument("code", nargs="?", help="例: SAGA-T101。省略すると一覧を表示")
    explain_p.add_argument("--language", default=argparse.SUPPRESS, metavar="LANG", help="表示言語（BCP 47。未対応言語は英語） / output locale")

    lsp_p = sub.add_parser("lsp", help="Language Server Protocolサーバーをstdioで起動 / start LSP server")
    lsp_p.add_argument("--language", default=argparse.SUPPRESS, metavar="LANG", help="診断表示言語（BCP 47） / diagnostic locale")

    debug_p = sub.add_parser("debug", help="文単位のトレース/ブレークポイント付きでSagaプログラムを実行")
    debug_p.add_argument("file")
    debug_p.add_argument("--trace", action="store_true", help="実行する文をすべてstderrへ表示")
    debug_p.add_argument("--break", dest="breakpoints", action="append", type=int, default=[], metavar="LINE", help="指定行を実行する直前のローカル値を表示（複数可）")
    debug_p.add_argument("--watch", dest="watches", action="append", default=[], metavar="NAME", help="各trace/breakで監視する変数名（複数可）")
    debug_p.add_argument("--record", metavar="FILE", help="statement境界のlocalsをbounded JSON traceへ保存")
    debug_p.add_argument("--max-events", type=int, default=100000, help="--recordの最大イベント数")
    debug_p.add_argument("--precision", type=int, default=50)
    debug_p.add_argument("--step-limit", type=int, default=None, help="任意の実行ステップ予算")
    debug_p.add_argument("--os-sandbox", choices=["off", "strict"], default="off")
    debug_p.add_argument("--language", default=argparse.SUPPRESS, metavar="LANG")
    _permission_args(debug_p)

    profile_p = sub.add_parser("profile", help="statement-level profiler: hit counts, interval time, peak managed-host memory")
    profile_p.add_argument("file")
    profile_p.add_argument("--json", dest="report_path", metavar="FILE", help="machine-readable profile report")
    profile_p.add_argument("--top", type=int, default=20)
    profile_p.add_argument("--precision", type=int, default=50)
    profile_p.add_argument("--step-limit", type=int, default=None)
    profile_p.add_argument("--os-sandbox", choices=["off", "strict"], default="off")
    profile_p.add_argument("--language", default=argparse.SUPPRESS, metavar="LANG")
    _permission_args(profile_p)

    build_p = sub.add_parser("build", help="compile Saga to native executable or WebAssembly")
    build_p.add_argument("file")
    build_p.add_argument("--target", choices=["native", "wasm"], required=True)
    build_p.add_argument("--profile", choices=["standard", "object", "codegen", "scalar"], default="standard", help="standard bundles the Go Standard Core runtime; object emits runtime-backed native objects; codegen lowers direct native function symbols with incremental link; scalar directly lowers a checked subset")
    build_p.add_argument("--build-dir", help="object/codegen profile cache/build directory")
    build_p.add_argument("--force", action="store_true", help="object/codegen profile: rebuild objects/link even when hashes match")
    build_p.add_argument("--output")

    reg_p = sub.add_parser("registry", help="reference Saga package registry server")
    reg_sub = reg_p.add_subparsers(dest="registry_action", required=True)
    reg_init = reg_sub.add_parser("init"); reg_init.add_argument("--root", default=".saga-registry"); reg_init.add_argument("--token", default=""); reg_init.set_defaults(require_signatures=True); reg_init.add_argument("--require-signatures", dest="require_signatures", action="store_true", help="require signed packages (default)"); reg_init.add_argument("--allow-unsigned-private", dest="require_signatures", action="store_false", help="private/lab compatibility only; not valid for public GA registry evidence")
    reg_key = reg_sub.add_parser("keygen"); reg_key.add_argument("--private", default="saga-publisher-private.pem"); reg_key.add_argument("--public", default="saga-publisher-public.pem")
    reg_serve = reg_sub.add_parser("serve"); reg_serve.add_argument("--root", default=".saga-registry"); reg_serve.add_argument("--host", default="127.0.0.1"); reg_serve.add_argument("--port", type=int, default=7331); reg_serve.add_argument("--token", default=None)

    pub_p = sub.add_parser("publish", help="publish a reproducible .sagapkg to a Saga registry")
    pub_p.add_argument("path", nargs="?", default="."); pub_p.add_argument("--registry", required=True); pub_p.add_argument("--token", default=""); pub_p.add_argument("--signing-key")
    search_p = sub.add_parser("search", help="search a Saga registry")
    search_p.add_argument("query"); search_p.add_argument("--registry", required=True)
    add_p = sub.add_parser("add", help="install signed name@version from a Saga registry into vendor/")
    add_p.add_argument("package"); add_p.add_argument("--registry", required=True); add_p.add_argument("--project", default="."); add_p.add_argument("--trust", default="", help="reviewed publisher SHA-256 fingerprint to trust and persist")

    mobile_p = sub.add_parser("mobile", help="generate native mobile runtime projects")
    mobile_sub = mobile_p.add_subparsers(dest="mobile_target", required=True)
    ios_p = mobile_sub.add_parser("ios"); ios_p.add_argument("file"); ios_p.add_argument("--output", required=True); ios_p.add_argument("--bundle-id", default="dev.saga.app")
    android_p = mobile_sub.add_parser("android"); android_p.add_argument("file"); android_p.add_argument("--output", required=True); android_p.add_argument("--application-id", default="dev.saga.app")

    eco_p = sub.add_parser("ecosystem", help="create third-party package authoring and bridge SDK")
    eco_p.add_argument("--output", default="saga-ecosystem-sdk")

    caps_p = sub.add_parser("capabilities", help="statically audit external capability categories used by a Saga program")
    caps_p.add_argument("file")
    caps_p.add_argument("--json", action="store_true")

    _add_standards_commands(sub)

    doctor_p = sub.add_parser("doctor", help="実行環境と標準機能を診断")
    doctor_p.add_argument("--json", action="store_true", help="診断結果をJSONで出力")

    args = parser.parse_args(argv)
    if args.command in {"run", "repl", "test", "debug", "profile"} and args.precision < 1:
        parser.error("--precision は1以上にしてください")
    if args.command in {"run", "test", "debug", "profile"} and args.step_limit is not None and args.step_limit < 1: parser.error("--step-limit は1以上にしてください")
    if args.command in {"run", "debug", "profile"} and getattr(args, "os_sandbox", "off") == "strict" and __import__("os").environ.get("SAGA_OS_SANDBOX_ACTIVE") != "1":
        from .sandbox import run_cli_in_strict_sandbox
        original = list(argv) if argv is not None else sys.argv[1:]
        return run_cli_in_strict_sandbox(original)
    source = ""; filename = "<input>"
    try:
        if args.command == "capabilities":
            from .capability_audit import audit
            result=audit(args.file)
            if args.json: print(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True))
            else:
                print("policy: deny-by-default")
                print("modules: "+(", ".join(result["modules"]) or "none"))
                print("capabilities: "+(", ".join(result["capabilities"]) or "none"))
            return 0
        if args.command == "build":
            from .aot import build, build_standard_bundle
            if args.profile == "object":
                if args.target != "native":
                    parser.error("--profile object currently supports --target native only")
                from .native_object import build_native_objects
                result = build_native_objects(args.file, args.output, build_dir=args.build_dir, force=args.force)
                print(result.output)
                print(f"objects: compiled={len(result.compiled_objects)} reused={len(result.reused_objects)} runtime_rebuilt={str(result.runtime_rebuilt).lower()} linked={str(result.linked).lower()}")
                print(result.report)
                return 0
            if args.profile == "codegen":
                if args.target != "native":
                    parser.error("--profile codegen currently supports --target native only")
                from .native_codegen import build_native_codegen
                result = build_native_codegen(args.file, args.output, build_dir=args.build_dir, force=args.force)
                print(result.output)
                print(f"direct-native: compiled={len(result.compiled_objects)} reused={len(result.reused_objects)} support_rebuilt={str(result.support_rebuilt).lower()} linked={str(result.linked).lower()} go_runtime=false")
                print(result.report)
                return 0
            result = build_standard_bundle(args.file, args.target, args.output) if args.profile == "standard" else build(args.file, args.target, args.output)
            print(result.output)
            if result.wit: print(result.wit)
            return 0
        if args.command == "production-check":
            from .production import production_check, write_report
            report = production_check(args.path, native=args.native, machine=args.machine)
            if args.report_path:
                write_report(report, args.report_path)
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if report["ready"] else CONFORMANCE_FAILURE
        if args.command == "registry":
            from .registry import init_registry, serve_registry, keygen
            if args.registry_action == "init": print(init_registry(args.root, args.token, args.require_signatures)); return 0
            if args.registry_action == "keygen":
                private, public = keygen(args.private, args.public); print(private); print(public); return 0
            server=serve_registry(args.root,args.host,args.port,args.token); print(f"Saga registry listening on http://{args.host}:{args.port}"); server.serve_forever(); return 0
        if args.command == "publish":
            from .registry import publish
            print(json.dumps(publish(args.path,args.registry,args.token,args.signing_key),ensure_ascii=False,indent=2)); return 0
        if args.command == "search":
            from .registry import search
            for item in search(args.registry,args.query):
                caps=",".join(item.get("capabilities",[])) or "none"; publisher=item.get("publisher_fingerprint","")[:16] or "unsigned"
                print(f"{item['name']}@{item['version']} {item['sha256']} capabilities={caps} publisher={publisher}")
            return 0
        if args.command == "add":
            from .registry import install
            print(install(args.registry,args.package,args.project,trust_once=args.trust)); return 0
        if args.command == "mobile":
            from .mobile import generate_ios, generate_android
            if args.mobile_target == "ios": print(generate_ios(args.file,args.output,args.bundle_id))
            else: print(generate_android(args.file,args.output,args.application_id))
            return 0
        if args.command == "ecosystem":
            from .ecosystem import create_package_sdk
            print(create_package_sdk(args.output)); return 0
        if args.command == "lsp":
            from .lsp import run_lsp
            return run_lsp(language=args.language)
        if args.command == "debug":
            from .debugger import debug_file
            if any(line < 1 for line in args.breakpoints):
                parser.error("--break は1以上の行番号にしてください")
            path = _source_path(args.file); filename = str(path); source = _read(path)
            debug_file(
                path, trace=args.trace, breakpoints=args.breakpoints, watches=args.watches,
                record_path=args.record, max_events=args.max_events, precision=args.precision,
                step_limit=args.step_limit, capabilities=_capabilities(args),
                debug_output=lambda text: print(text, file=sys.stderr),
            )
            return 0
        if args.command == "profile":
            from .debugger import profile_file
            path = _source_path(args.file); filename = str(path); source = _read(path)
            report = profile_file(
                path, precision=args.precision, step_limit=args.step_limit,
                capabilities=_capabilities(args), report_path=args.report_path, top=args.top,
            )
            for row in report["top"]:
                ms = row["interval_ns"] / 1_000_000
                print(f"{ms:10.3f} ms  hits={row['hits']:6d}  {row['file']}:{row['line']}:{row['column']}")
            print(f"elapsed={report['elapsed_ns']/1_000_000:.3f} ms peak_host_heap={report['python_heap_peak_bytes']} bytes")
            return 0
        if args.command == "explain":
            language = normalize_language(args.language)
            if not args.code:
                for spec in all_specs():
                    print(f"{spec.id}  {spec.title(language)}")
                return 0
            spec = get_spec(args.code.upper())
            if spec is None:
                print(("Unknown diagnostic code: " if language == "en" else "未登録の診断コードです: ") + args.code, file=sys.stderr)
                return INPUT_ERROR
            print(f"{spec.id}: {spec.title(language)}")
            print()
            print(spec.explanation(language))
            if spec.help(language):
                print()
                print(("Suggested fix: " if language == "en" else "修正案: ") + str(spec.help(language)))
            return 0
        if args.command == "standards":
            return _run_standards(args)
        if args.command == "run":
            path = _source_path(args.file); filename = str(path); source = _read(path)
            run_file(str(path), precision=args.precision, step_limit=args.step_limit, capabilities=_capabilities(args))
        elif args.command == "check":
            path = _source_path(args.file); filename = str(path); source = _read(path); loaded = compile_file(str(path)); program = loaded.program
            if args.standard:
                diagnostics = lint_program(program, standard=True)
                for item in diagnostics: print(item.render(filename), file=sys.stderr)
                if any(item.severity == "error" for item in diagnostics): return 1
            print(f"OK: {filename}")
        elif args.command == "new": _new_project(args.name, args.template)
        elif args.command == "module":
            from .module_interface import build_module_interface, load_module_interface
            if args.module_action == "compile":
                data = build_module_interface(args.file, output=args.output, root=args.root)
                target = pathlib.Path(args.output) if args.output else pathlib.Path(args.file).with_suffix(".smi.json")
                print(json.dumps({"interface": str(target), "module": data["module"], "abi_sha256": data["abi_sha256"], "build_sha256": data["build_sha256"]}, ensure_ascii=False))
                return 0
            data = load_module_interface(args.interface, source=args.source)
            print(json.dumps({"valid": True, "module": data["module"], "abi_sha256": data["abi_sha256"]}, ensure_ascii=False))
            return 0
        elif args.command == "modules":
            if args.module:
                if args.module not in MODULES:
                    print(f"Unknown module: {args.module}", file=sys.stderr); return 1
                for name in sorted(MODULES[args.module].functions): print(f"{args.module}.{name}")
            else:
                for name in sorted(MODULES): print(f"{name:12} {len(MODULES[name].functions):>2} functions")
        elif args.command == "metadata":
            path = _source_path(args.file); filename = str(path); source = _read(path)
            print(json.dumps(extract_metadata(compile_file(str(path)).program), ensure_ascii=False, indent=2))
        elif args.command == "process":
            path = _source_path(args.file); filename = str(path); source = _read(path)
            metadata = extract_metadata(compile_file(str(path)).program)
            processor_path = pathlib.Path(args.processor).expanduser().resolve()
            output_dir = pathlib.Path(args.output).expanduser().resolve(); output_dir.mkdir(parents=True, exist_ok=True)
            if args.unsafe_processor:
                spec = importlib.util.spec_from_file_location(f"saga_processor_{processor_path.stem}", processor_path)
                if spec is None or spec.loader is None: raise OSError("プロセッサを読み込めません")
                module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
                processor = getattr(module, "process", None)
                if not callable(processor): raise OSError("legacy processor requires process(metadata, output_dir)")
                processor(metadata, output_dir)
            else:
                from .processor_runtime import run_processor, write_outputs
                write_outputs(output_dir, run_processor(processor_path, metadata))
            print(f"Processed: {filename} -> {output_dir}")
        elif args.command == "lint":
            files = saga_files(args.path)
            if not files:
                raise OSError(f"Sagaソースがありません: {args.path}")
            failed = False
            for path in files:
                file_source = _read(path)
                try:
                    program = compile_file(str(path)).program
                except SourceError as exc:
                    print(format_diagnostic(exc, file_source, language=normalize_language(args.language)), file=sys.stderr); failed = True; continue
                diagnostics = lint_program(program, standard=args.standard)
                for item in diagnostics:
                    print(item.render(str(path)))
                if any(item.severity == "error" for item in diagnostics) or (args.deny_warnings and diagnostics): failed = True
            return 1 if failed else 0
        elif args.command == "fmt":
            files = saga_files(args.path)
            if not files:
                raise OSError(f"Sagaソースがありません: {args.path}")
            changed = []
            for path in files:
                original = _read(path); formatted = format_source(original)
                parse_source(formatted, str(path))
                if original != formatted:
                    changed.append(path)
                    if not args.check: path.write_text(formatted, encoding="utf-8")
            for path in changed: print(("Would format" if args.check else "Formatted") + f": {path}")
            return 1 if args.check and changed else 0
        elif args.command == "migrate":
            from .migration import migrate_source
            files = saga_files(args.path)
            if not files:
                raise OSError(f"Sagaソースがありません: {args.path}")
            total = 0
            for path in files:
                original = _read(path); result = migrate_source(original)
                for change in result.changes:
                    print(f"{path}:{change.line}: {change.reason}")
                    print(f"  - {change.before.strip()}")
                    print(f"  + {change.after.strip()}")
                if result.changes:
                    total += len(result.changes)
                    # Parse before replacing so a migration can never write an
                    # invalid file merely because the textual rule matched.
                    parse_source(result.source, str(path))
                    if args.write:
                        path.write_text(result.source, encoding="utf-8")
            print(f"{total} safe migration(s)" + (" written" if args.write else " found"))
            return 0
        elif args.command == "test":
            raw_target = pathlib.Path(args.path).expanduser()
            if raw_target.is_symlink():
                raise OSError(f"Sagaテストの列挙元にシンボリックリンクは使用できません: {raw_target}")
            target = raw_target
            if target.is_dir():
                project = find_project(target)
                if project is not None and target.resolve() == project.root:
                    target = project.test_dir
            files = saga_files(target)
            if not files:
                raise OSError(f"Sagaテストがありません: {target}")
            failures = 0
            for path in files:
                file_source = _read(path); captured: list[str] = []
                try:
                    run_file(str(path), output=captured.append, precision=args.precision, step_limit=args.step_limit, capabilities=_capabilities(args))
                    print(f"PASS {path}")
                except SourceError as exc:
                    failures += 1; print(f"FAIL {path}", file=sys.stderr); print(format_diagnostic(exc, file_source, language=normalize_language(args.language)), file=sys.stderr)
            print(f"{len(files) - failures} passed, {failures} failed")
            return 1 if failures else 0
        elif args.command == "lock":
            result = build_lock(args.path); print(f"Locked: {result.path}")
        elif args.command == "verify":
            valid, errors = verify_lock(args.path)
            if valid: print("LOCK_OK"); return 0
            for item in errors: print(item, file=sys.stderr)
            return CONFORMANCE_FAILURE
        elif args.command == "pack":
            print(pack_project(args.path, args.output))
        elif args.command == "info":
            result = {
                "language": "Saga", "language_version": "1.0", "implementation": VERSION,
                "profile": "Standard Core + Hosted Libraries", "unicode": "15.1.0",
                "resource_model": RESOURCE_MODEL,
                "normative_resource_limits": {},
                "parallelism": {
                    "isolated_threads": True,
                    "cpu_processes": True,
                    "worker_ceiling": "none-defined-by-Saga",
                },
                "closures": {"lexical": True, "mutable_capture_cells": True},
                "packages": {"registry_protocol": "v1", "sha256": True, "ed25519_publishers": True, "capability_metadata": True},
                "compilers": {"native_standard_runtime_aot": True, "native_object_incremental": True, "native_codegen_abi_032": True, "direct_cross_module_symbols": True, "wasm_wasi_runtime_aot": True, "scalar_direct_c": True},
                "ecosystem_bridges": {"isolated_python_allowlist": True, "wit_component_sdk": True},
                "mobile": {"standard_core_runtime_source": ["ios", "android"], "lightweight_scalar_runtime": ["ios", "android"]},
                "diagnostics": {
                    "schema": 2,
                    "formats": ["text", "json", "sarif"],
                    "locales": ["en", "ja"],
                    "detailed_ids": True,
                    "explain_command": True,
                    "lsp": "stdio-publishDiagnostics",
                },
                "internationalization": {
                    "identifier_profile": "Unicode 15.1 XID + NFC",
                    "project_names": "NFC Unicode XID components separated by hyphen",
                    "source_encoding": "UTF-8",
                },
            }
            if args.json: print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(f"Saga language {result['language_version']} / implementation {VERSION}")
                print(result["profile"]); print(f"Unicode {result['unicode']}")
        elif args.command == "conformance":
            from .self_conformance import run_self_conformance
            report = run_self_conformance()
            if args.json: print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                for case in report["cases"]: print(f"{'PASS' if case['pass'] else 'FAIL'} {case['id']}")
                print(f"{report['passed']}/{report['total']} passed")
            return 0 if report["pass"] else CONFORMANCE_FAILURE
        elif args.command == "doctor":
            optional = {
                "tkinter": "GUI", "PIL": "image", "cv2": "video", "cryptography": "AES-GCM",
                "websocket": "WebSocket", "pygame": "game", "boto3": "cloud", "gpiozero": "IoT", "pyspark": "Spark",
            }
            result = {
                "saga": VERSION,
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.platform(),
                "architecture": platform.machine(),
                "executable": sys.executable,
                "unicode_database": unicodedata.unidata_version,
                "unicode_profile": "15.1.0-vendored",
                "unicode_profile_ok": True,
                "optional": {label: importlib.util.find_spec(module) is not None for module, label in optional.items()},
                "commands": {name: shutil.which(name) is not None for name in ("git", "go", "clang", "node", "swift", "gomobile", "java", "gcc")},
            }
            if args.json: print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"Saga {VERSION}")
                print(f"Python {result['python']} ({result['implementation']})")
                print(f"Platform: {result['platform']} / {result['architecture']}")
                print(f"Unicode host: {result['unicode_database']} / Saga profile: {result['unicode_profile']} (OK)")
                for name, available in result["optional"].items(): print(f"{name:10} {'OK' if available else 'not installed'}")
        elif args.command == "repl":
            print(f"Saga {VERSION} REPL — 状態は保持されます。終了は :quit")
            session = SagaSession("<repl>", precision=args.precision)
            try:
                while True:
                    try: line = input("saga> ")
                    except EOFError: print(); break
                    if line.strip() in {":quit", ":q"}: break
                    if not line.strip(): continue
                    try: session.execute(line)
                    except SourceError as exc: print(format_diagnostic(exc, line, language=normalize_language(args.language)), file=sys.stderr)
            finally:
                session.close()
    except (StandardsError, PackageError) as exc:
        print(f"エラー: {exc}", file=sys.stderr); return INPUT_ERROR
    except SourceError as exc:
        print(_render_error(exc, source, getattr(args, "diagnostic_format", "text"), getattr(args, "language", "auto")), file=sys.stderr); return for_error(exc)
    except FileExistsError:
        print(f"エラー: '{args.name}' はすでに存在します", file=sys.stderr); return INPUT_ERROR
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"ファイルエラー: {exc}", file=sys.stderr); return INPUT_ERROR
    except Exception as exc:
        if getattr(args, "debug", False):
            raise
        internal = InternalLanguageError("処理系内部で予期しない障害が発生しました", 1, 1, filename, "--debugで開発者向け詳細を確認できます")
        print(_render_error(internal, source, getattr(args, "diagnostic_format", "text"), getattr(args, "language", "auto")), file=sys.stderr)
        return INTERNAL_ERROR
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
