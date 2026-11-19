from __future__ import annotations
from dataclasses import dataclass
from contextlib import closing
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, parse_qs
from urllib.request import Request, build_opener, ProxyHandler, HTTPHandler, HTTPSHandler, HTTPRedirectHandler
from urllib.error import HTTPError
import json, os, re, shutil, tempfile, base64, stat, hmac, threading, zipfile, io, tomllib, ssl, ipaddress, sqlite3

from .package import pack_project, build_lock, verify_lock, PackageError, _strict_json_loads
from .project import SEMVER_RE, valid_project_name
from .file_lock import exclusive_file_lock

NAME_RE=re.compile(r'^[^/\\\x00]+$')
REGISTRY_MAX_PACKAGE_BYTES = 96 << 20
REGISTRY_MAX_METADATA_BYTES = 8 << 20
REGISTRY_MAX_EXTRACTED_BYTES = 256 << 20
REGISTRY_MAX_EXTRACTED_FILES = 10_000
REGISTRY_SEARCH_LIMIT = 200
REGISTRY_INDEX_SCHEMA = 2

class _NoRegistryRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

_REGISTRY_OPENER = build_opener(
    ProxyHandler({}),
    HTTPHandler(),
    HTTPSHandler(context=ssl.create_default_context()),
    _NoRegistryRedirect(),
)

def urlopen(req, timeout=30):
    """Registry-only opener: no ambient proxies and no automatic redirects."""
    return _REGISTRY_OPENER.open(req, timeout=timeout)

def _validate_registry_url(registry: str) -> str:
    raw=str(registry).strip().rstrip('/')
    u=urlparse(raw)
    if u.username is not None or u.password is not None or u.query or u.fragment:
        raise ValueError('registry URL must not contain credentials, query parameters, or a fragment')
    if u.scheme == 'https' and u.hostname:
        return raw
    if u.scheme == 'http' and u.hostname:
        host=u.hostname.rstrip('.').lower()
        local=host == 'localhost' or host.endswith('.localhost')
        if not local:
            try: local=ipaddress.ip_address(host).is_loopback
            except ValueError: local=False
        if local:
            return raw
    raise ValueError('registry URL must use HTTPS; plain HTTP is allowed only for loopback development')

def _portable_zip_path(name: str) -> str:
    if not name or '\x00' in name or '\\' in name:
        raise ValueError('unsafe package path')
    from pathlib import PurePosixPath
    p=PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts:
        raise ValueError('unsafe package path')
    normalized=p.as_posix()
    if normalized in {'', '.'} or normalized != name:
        raise ValueError('package path must be canonical')
    return normalized

@dataclass(frozen=True,slots=True)
class RegistryPackage:
    name:str; version:str; sha256:str; size:int; capabilities:tuple[str,...]=()

def _safe(s:str)->str:
    if not s or not NAME_RE.match(s) or s in {'.','..'}: raise ValueError('invalid package name/version')
    return s

def _safe_name(s: str) -> str:
    s = _safe(s)
    if not valid_project_name(s): raise ValueError('invalid package name')
    return s

def _safe_version(s: str) -> str:
    s = _safe(s)
    if not SEMVER_RE.fullmatch(s): raise ValueError('invalid package version')
    return s

def _read_limited(response, limit: int) -> bytes:
    length = response.headers.get('Content-Length')
    if length:
        try:
            if int(length) > limit: raise ValueError(f'registry response exceeds {limit} bytes')
        except ValueError as exc:
            if 'exceeds' in str(exc): raise
            raise ValueError('invalid registry Content-Length') from exc
    data = response.read(limit + 1)
    if len(data) > limit: raise ValueError(f'registry response exceeds {limit} bytes')
    return data

def _read_path_limited(path: Path, limit: int) -> bytes:
    st=path.stat()
    if st.st_size > limit: raise ValueError(f'registry stored file exceeds {limit} bytes')
    with path.open('rb') as f:
        data=f.read(limit+1)
    if len(data)>limit or len(data)!=st.st_size: raise ValueError('registry stored file size mismatch')
    return data

def keygen(private_path: str | Path, public_path: str | Path) -> tuple[Path, Path]:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
    except ImportError as exc:
        raise RuntimeError("cryptography is required for publisher signing") from exc
    priv=Ed25519PrivateKey.generate(); pub=priv.public_key()
    pp=Path(private_path).resolve(); qp=Path(public_path).resolve(); pp.parent.mkdir(parents=True,exist_ok=True); qp.parent.mkdir(parents=True,exist_ok=True)
    if pp.exists() or qp.exists(): raise FileExistsError('refusing to overwrite an existing publisher key path')
    private_bytes=priv.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL
    if hasattr(os,'O_NOFOLLOW'): flags |= os.O_NOFOLLOW
    fd=os.open(pp,flags,0o600)
    try:
        if hasattr(os,'fchmod'): os.fchmod(fd,0o600)
        with os.fdopen(fd,'wb',closefd=False) as f: f.write(private_bytes); f.flush(); os.fsync(f.fileno())
    finally:
        os.close(fd)
    if os.name != 'nt': os.chmod(pp,0o600)
    public_bytes=pub.public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    try:
        qflags=os.O_WRONLY|os.O_CREAT|os.O_EXCL
        if hasattr(os,'O_NOFOLLOW'): qflags |= os.O_NOFOLLOW
        qfd=os.open(qp,qflags,0o644)
        try:
            with os.fdopen(qfd,'wb',closefd=False) as f: f.write(public_bytes); f.flush(); os.fsync(f.fileno())
        finally: os.close(qfd)
    except Exception:
        pp.unlink(missing_ok=True); raise
    return pp,qp

def _sign(data: bytes, private_path: str | Path) -> tuple[str,str,str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    key=load_pem_private_key(Path(private_path).read_bytes(),password=None)
    sig=key.sign(data); pub=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    fp=sha256(pub).hexdigest()
    return base64.b64encode(sig).decode(),base64.b64encode(pub).decode(),fp

def _verify_signature(data: bytes, signature: str, public_key: str) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    pub=base64.b64decode(public_key); sig=base64.b64decode(signature)
    Ed25519PublicKey.from_public_bytes(pub).verify(sig,data)

def _atomic_private_text(path: Path, text: str) -> None:
    path=path.resolve(); path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp_name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    tmp=Path(tmp_name)
    try:
        if hasattr(os,'fchmod'): os.fchmod(fd,0o600)
        with os.fdopen(fd,'w',encoding='utf-8',closefd=False) as f:
            f.write(text); f.flush(); os.fsync(f.fileno())
        os.close(fd); fd=-1
        os.replace(tmp,path)
        if os.name!='nt': os.chmod(path,0o600)
    finally:
        if fd>=0: os.close(fd)
        tmp.unlink(missing_ok=True)

def _trust_store_path(project: str | Path) -> Path:
    return Path(project).resolve() / "saga.trust.json"

def _load_trust_store(project: str | Path) -> set[str]:
    p = _trust_store_path(project)
    if not p.exists(): return set()
    try: doc=json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc: raise ValueError("invalid registry trust store") from exc
    vals=doc.get("fingerprints",[]) if isinstance(doc,dict) else []
    out=set()
    for fp in vals:
        fp=str(fp).strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}",fp): out.add(fp)
    return out

def trust_fingerprint(project: str | Path, fingerprint: str) -> Path:
    fp=str(fingerprint).strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}",fp) is None: raise ValueError("publisher fingerprint must be 64 hexadecimal characters")
    p=_trust_store_path(project); p.parent.mkdir(parents=True,exist_ok=True)
    with exclusive_file_lock(p.with_name(p.name+'.lock')):
        vals=_load_trust_store(project); vals.add(fp)
        _atomic_private_text(p,json.dumps({"schema":1,"fingerprints":sorted(vals)},indent=2,sort_keys=True)+"\n")
    return p

def _archive_identity(data: bytes) -> tuple[str,str]:
    if len(data)>REGISTRY_MAX_PACKAGE_BYTES: raise ValueError("package exceeds registry size limit")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos=z.infolist()
        if len(infos)>REGISTRY_MAX_EXTRACTED_FILES: raise ValueError("package contains too many files")
        total=0; seen=set(); file_infos={}
        for info in infos:
            rel=_portable_zip_path(info.filename.rstrip('/') or info.filename)
            if rel in seen: raise ValueError("duplicate package path")
            seen.add(rel); total += info.file_size
            mode=(info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode): raise ValueError("package symlinks are not allowed")
            if info.file_size>REGISTRY_MAX_PACKAGE_BYTES or total>REGISTRY_MAX_EXTRACTED_BYTES: raise ValueError("package expanded content exceeds safety limit")
            if not info.is_dir(): file_infos[rel]=info
        try:
            manifest=tomllib.loads(z.read("saga.toml").decode("utf-8")).get("project",{})
            lock=_strict_json_loads(z.read("saga.lock").decode("utf-8")); locked=lock.get("project",{})
        except Exception as exc: raise ValueError("package is missing or has malformed saga.toml/saga.lock") from exc
        name,version=manifest.get("name"),manifest.get("version")
        if locked.get("name")!=name or locked.get("version")!=version: raise ValueError("package manifest/lock identity mismatch")
        records=lock.get("files") if isinstance(lock,dict) else None
        if not isinstance(records,list): raise ValueError("package lock files must be an array")
        expected={"saga.lock"}; tracked=set()
        for record in records:
            if not isinstance(record,dict) or not isinstance(record.get("path"),str): raise ValueError("package lock contains invalid file record")
            rel=_portable_zip_path(record["path"])
            if rel in tracked: raise ValueError("package lock contains duplicate file path")
            tracked.add(rel); expected.add(rel)
            info=file_infos.get(rel)
            if info is None: raise ValueError(f"package lock tracked file is missing: {rel}")
            content=z.read(info)
            if len(content)!=info.file_size: raise ValueError("package file size mismatch")
            if record.get("size")!=len(content) or record.get("sha256")!=sha256(content).hexdigest():
                raise ValueError(f"package content does not match saga.lock: {rel}")
        if "saga.toml" not in tracked: raise ValueError("package lock does not track saga.toml")
        extras=set(file_infos)-expected
        if extras: raise ValueError("package contains files not tracked by saga.lock: "+", ".join(sorted(extras)[:5]))
        return _safe_name(str(name)), _safe_version(str(version))

def _index_path(root: Path) -> Path:
    return root / "index.sqlite3"

def _index_connect(root: Path) -> sqlite3.Connection:
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(_index_path(root), timeout=10.0)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=10000")
    db.execute("CREATE TABLE IF NOT EXISTS registry_meta(schema INTEGER NOT NULL)")
    db.execute("""CREATE TABLE IF NOT EXISTS packages(
        name TEXT NOT NULL, name_fold TEXT NOT NULL, version TEXT NOT NULL,
        sha256 TEXT NOT NULL, size INTEGER NOT NULL, capabilities TEXT NOT NULL,
        publisher_fingerprint TEXT NOT NULL, metadata_path TEXT NOT NULL,
        PRIMARY KEY(name, version)
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS packages_name_fold_idx ON packages(name_fold, name, version)")
    previous = db.execute("SELECT schema FROM registry_meta LIMIT 1").fetchone()
    fts = False
    try:
        # FTS5's trigram tokenizer gives substring search an actual index. Keep
        # a plain-SQL fallback for SQLite builds without FTS5/trigram support.
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS packages_fts USING fts5(name_fold, content='packages', content_rowid='rowid', tokenize='trigram')")
        db.executescript("""
        CREATE TRIGGER IF NOT EXISTS packages_ai AFTER INSERT ON packages BEGIN
          INSERT INTO packages_fts(rowid,name_fold) VALUES (new.rowid,new.name_fold);
        END;
        CREATE TRIGGER IF NOT EXISTS packages_ad AFTER DELETE ON packages BEGIN
          INSERT INTO packages_fts(packages_fts,rowid,name_fold) VALUES('delete',old.rowid,old.name_fold);
        END;
        CREATE TRIGGER IF NOT EXISTS packages_au AFTER UPDATE ON packages BEGIN
          INSERT INTO packages_fts(packages_fts,rowid,name_fold) VALUES('delete',old.rowid,old.name_fold);
          INSERT INTO packages_fts(rowid,name_fold) VALUES (new.rowid,new.name_fold);
        END;
        """)
        fts = True
    except sqlite3.OperationalError:
        fts = False
    target_schema = REGISTRY_INDEX_SCHEMA if fts else 1
    if previous is None:
        db.execute("DELETE FROM registry_meta")
        db.execute("INSERT INTO registry_meta(schema) VALUES (?)", (target_schema,))
        if fts:
            db.execute("INSERT INTO packages_fts(packages_fts) VALUES('rebuild')")
    elif int(previous[0]) != target_schema:
        if fts:
            db.execute("INSERT INTO packages_fts(packages_fts) VALUES('rebuild')")
        db.execute("UPDATE registry_meta SET schema=?", (target_schema,))
    db.commit()
    return db

def _index_has_fts(db: sqlite3.Connection) -> bool:
    row = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='packages_fts'").fetchone()
    return row is not None

def _index_upsert(root: Path, meta: dict, metadata_path: Path) -> None:
    rel = metadata_path.resolve().relative_to(root.resolve()).as_posix()
    with closing(_index_connect(root)) as db:
        db.execute("""INSERT INTO packages(name,name_fold,version,sha256,size,capabilities,publisher_fingerprint,metadata_path)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(name,version) DO UPDATE SET
              name_fold=excluded.name_fold,sha256=excluded.sha256,size=excluded.size,
              capabilities=excluded.capabilities,publisher_fingerprint=excluded.publisher_fingerprint,metadata_path=excluded.metadata_path
        """, (str(meta['name']), str(meta['name']).casefold(), str(meta['version']), str(meta['sha256']), int(meta['size']),
                json.dumps(sorted(meta.get('capabilities',[])), separators=(',',':')), str(meta.get('publisher_fingerprint','')), rel))
        db.commit()

def _index_rebuild_if_empty(root: Path) -> None:
    with closing(_index_connect(root)) as db:
        count = db.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
    if count:
        return
    for path in (root/'packages').glob('*/*/metadata.json'):
        try:
            meta=json.loads(path.read_text(encoding='utf-8'))
            _safe_name(str(meta.get('name',''))); _safe_version(str(meta.get('version','')))
            _index_upsert(root,meta,path)
        except Exception:
            continue

def _index_candidates(root: Path, query: str, limit: int = REGISTRY_SEARCH_LIMIT) -> list[Path]:
    _index_rebuild_if_empty(root)
    q = str(query).casefold()
    # Escape LIKE metacharacters so the user's text remains a substring query.
    like = '%' + q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
    with closing(_index_connect(root)) as db:
        if q and len(q) >= 3 and _index_has_fts(db):
            phrase='"' + q.replace('"','""') + '"'
            rows=db.execute("""SELECT p.metadata_path FROM packages_fts f
                JOIN packages p ON p.rowid=f.rowid
                WHERE packages_fts MATCH ? ORDER BY p.name,p.version LIMIT ?""", (phrase, int(limit))).fetchall()
        else:
            rows=db.execute("SELECT metadata_path FROM packages WHERE name_fold LIKE ? ESCAPE '\\' ORDER BY name,version LIMIT ?", (like, int(limit))).fetchall()
    out=[]
    base=root.resolve()
    for (rel,) in rows:
        candidate=(root/str(rel)).resolve()
        try: candidate.relative_to(base)
        except ValueError: continue
        out.append(candidate)
    return out

def registry_index_stats(root: str | Path) -> dict:
    base=Path(root).resolve(); _index_rebuild_if_empty(base)
    with closing(_index_connect(base)) as db:
        packages=int(db.execute("SELECT COUNT(*) FROM packages").fetchone()[0])
        names=int(db.execute("SELECT COUNT(DISTINCT name) FROM packages").fetchone()[0])
        backend="fts5-trigram" if _index_has_fts(db) else "sql-like-fallback"
        schema=int(db.execute("SELECT schema FROM registry_meta LIMIT 1").fetchone()[0])
    return {"schema":schema,"packages":packages,"names":names,"search_limit":REGISTRY_SEARCH_LIMIT,"search_backend":backend}

def init_registry(root:str|Path, token:str|None=None, require_signatures:bool=True)->Path:
    root=Path(root).resolve(); (root/'packages').mkdir(parents=True,exist_ok=True)
    raw_token=token if token is not None else os.environ.get('SAGA_REGISTRY_TOKEN','')
    config={'schema':2,'token_sha256':sha256(raw_token.encode('utf-8')).hexdigest() if raw_token else '','visibility':'private-reference','require_signatures':bool(require_signatures)}
    _atomic_private_text(root/'registry.json',json.dumps(config,indent=2,sort_keys=True)+'\n')
    with closing(_index_connect(root)):
        pass
    return root

def _meta(root:Path,name:str,version:str): return root/'packages'/_safe_name(name)/_safe_version(version)/'metadata.json'

def serve_registry(root:str|Path,host='127.0.0.1',port=7331,token:str|None=None,require_signatures:bool|None=None):
    root=Path(root).resolve()
    if not (root/'registry.json').exists(): init_registry(root,token,True if require_signatures is None else require_signatures)
    try: cfg=json.loads((root/'registry.json').read_text(encoding='utf-8'))
    except Exception as exc: raise ValueError('invalid registry configuration') from exc
    explicit_token=token if token is not None else os.environ.get('SAGA_REGISTRY_TOKEN','')
    expected_hash=sha256(explicit_token.encode('utf-8')).hexdigest() if explicit_token else str(cfg.get('token_sha256',''))
    legacy_token=cfg.get('token')
    if not expected_hash and isinstance(legacy_token,str) and legacy_token:
        expected_hash=sha256(legacy_token.encode('utf-8')).hexdigest()
        cfg.pop('token',None); cfg['schema']=2; cfg['token_sha256']=expected_hash
        _atomic_private_text(root/'registry.json',json.dumps(cfg,indent=2,sort_keys=True)+'\n')
    require_signed=bool(cfg.get('require_signatures',False) if require_signatures is None else require_signatures)
    publish_lock=threading.Lock()
    class H(BaseHTTPRequestHandler):
        server_version='SagaRegistry/1.0'
        def setup(self):
            super().setup(); self.connection.settimeout(30)
        def _json(self,status,obj):
            data=json.dumps(obj,ensure_ascii=False,sort_keys=True).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
        def log_message(self,*a): pass
        def do_GET(self):
            u=urlparse(self.path); parts=[unquote(x) for x in u.path.split('/') if x]
            if parts==['healthz']: return self._json(200,{'status':'ok','schema':1,'protocol':'v1'})
            if parts==['v1','search']:
                q=parse_qs(u.query).get('q',[''])[0].casefold(); out=[]
                for m in _index_candidates(root,q):
                    try:
                        d=json.loads(m.read_text(encoding='utf-8')); pkg=m.parent/'package.sagapkg'; data=_read_path_limited(pkg,REGISTRY_MAX_PACKAGE_BYTES)
                        if sha256(data).hexdigest()!=d.get('sha256'): continue
                        name,ver=_archive_identity(data)
                        if name!=d.get('name') or ver!=d.get('version'): continue
                        if d.get('signature') or d.get('publisher_key') or d.get('publisher_fingerprint'):
                            if not (d.get('signature') and d.get('publisher_key') and d.get('publisher_fingerprint')): continue
                            _verify_signature(data,d['signature'],d['publisher_key'])
                            actual_fp=sha256(base64.b64decode(d['publisher_key'],validate=True)).hexdigest()
                            if not hmac.compare_digest(str(d['publisher_fingerprint']).lower(),actual_fp): continue
                    except Exception: continue
                    if q in str(d.get('name','')).casefold(): out.append(d)
                return self._json(200,{'packages':sorted(out,key=lambda x:(x['name'],x['version']))})
            if len(parts)==4 and parts[:2]==['v1','packages']:
                try: name,ver=_safe_name(parts[2]),_safe_version(parts[3])
                except ValueError: return self._json(400,{'error':'invalid package identity'})
                meta=_meta(root,name,ver)
                if not meta.exists(): return self._json(404,{'error':'not found'})
                pkg=meta.parent/'package.sagapkg'
                try: data=_read_path_limited(pkg,REGISTRY_MAX_PACKAGE_BYTES); md=json.loads(meta.read_text(encoding='utf-8'))
                except Exception: return self._json(500,{'error':'stored package metadata failure'})
                if sha256(data).hexdigest()!=md.get('sha256'): return self._json(500,{'error':'stored package integrity failure'})
                try:
                    stored_name,stored_ver=_archive_identity(data)
                    if stored_name!=name or stored_ver!=ver: raise ValueError('stored identity mismatch')
                    if md.get('signature') or md.get('publisher_key') or md.get('publisher_fingerprint'):
                        if not (md.get('signature') and md.get('publisher_key') and md.get('publisher_fingerprint')): raise ValueError('incomplete stored signature')
                        _verify_signature(data,md['signature'],md['publisher_key'])
                        actual_fp=sha256(base64.b64decode(md['publisher_key'],validate=True)).hexdigest()
                        if not hmac.compare_digest(str(md['publisher_fingerprint']).lower(),actual_fp): raise ValueError('stored fingerprint mismatch')
                except Exception: return self._json(500,{'error':'stored package integrity failure'})
                self.send_response(200); self.send_header('Content-Type','application/vnd.saga.package'); self.send_header('X-Content-Type-Options','nosniff'); self.send_header('X-Saga-Sha256',md['sha256'])
                if md.get('signature'): self.send_header('X-Saga-Signature',md['signature'])
                if md.get('publisher_key'): self.send_header('X-Saga-Publisher-Key',md['publisher_key'])
                if md.get('publisher_fingerprint'): self.send_header('X-Saga-Publisher-Fingerprint',md['publisher_fingerprint'])
                self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
            return self._json(404,{'error':'not found'})
        def do_PUT(self):
            parts=[unquote(x) for x in urlparse(self.path).path.split('/') if x]
            if len(parts)!=4 or parts[:2]!=['v1','packages']: return self._json(404,{'error':'not found'})
            if not expected_hash: return self._json(503,{'error':'publishing disabled until registry token is configured'})
            auth=self.headers.get('Authorization','')
            presented=auth[7:] if auth.startswith('Bearer ') else ''
            presented_hash=sha256(presented.encode('utf-8')).hexdigest() if presented else ''
            if not hmac.compare_digest(presented_hash,expected_hash): return self._json(401,{'error':'unauthorized'})
            try: name,ver=_safe_name(parts[2]),_safe_version(parts[3])
            except ValueError: return self._json(400,{'error':'invalid package identity'})
            try: n=int(self.headers.get('Content-Length',''))
            except Exception: return self._json(411,{'error':'valid Content-Length required'})
            if n < 0 or n > REGISTRY_MAX_PACKAGE_BYTES: return self._json(413,{'error':'package too large'})
            data=self.rfile.read(n)
            if len(data) != n: return self._json(400,{'error':'incomplete package body'})
            digest=sha256(data).hexdigest(); supplied=self.headers.get('X-Saga-Sha256')
            if not supplied or not hmac.compare_digest(supplied.lower(),digest): return self._json(400,{'error':'sha256 missing or mismatch'})
            try: inner_name,inner_ver=_archive_identity(data)
            except Exception as exc: return self._json(400,{'error':'invalid package: '+str(exc)})
            if inner_name!=name or inner_ver!=ver: return self._json(400,{'error':'package identity mismatch'})
            sig=self.headers.get('X-Saga-Signature'); pub=self.headers.get('X-Saga-Publisher-Key'); fp=self.headers.get('X-Saga-Publisher-Fingerprint')
            meta={'name':name,'version':ver,'sha256':digest,'size':len(data),'capabilities':sorted(x for x in self.headers.get('X-Saga-Capabilities','').split(',') if x)}
            if sig or pub or fp:
                if not (sig and pub and fp): return self._json(400,{'error':'incomplete publisher signature metadata'})
                try: _verify_signature(data,sig,pub); actual_fp=sha256(base64.b64decode(pub,validate=True)).hexdigest()
                except Exception: return self._json(400,{'error':'invalid publisher signature'})
                if not hmac.compare_digest(fp.lower(),actual_fp): return self._json(400,{'error':'publisher fingerprint mismatch'})
                meta.update({'signature':sig,'publisher_key':pub,'publisher_fingerprint':actual_fp})
            elif require_signed: return self._json(400,{'error':'signed package required'})
            d=(root/'packages'/name/ver); parent=d.parent; parent.mkdir(parents=True,exist_ok=True)
            with publish_lock:
                if d.exists():
                    try:
                        old=json.loads((d/'metadata.json').read_text(encoding='utf-8'))
                        stored=_read_path_limited(d/'package.sagapkg',REGISTRY_MAX_PACKAGE_BYTES)
                        stored_digest=sha256(stored).hexdigest()
                        if stored_digest!=old.get('sha256'): raise ValueError('stored digest mismatch')
                        stored_name,stored_ver=_archive_identity(stored)
                        if stored_name!=name or stored_ver!=ver: raise ValueError('stored identity mismatch')
                        if old.get('signature') or old.get('publisher_key') or old.get('publisher_fingerprint'):
                            if not (old.get('signature') and old.get('publisher_key') and old.get('publisher_fingerprint')): raise ValueError('incomplete stored signature')
                            _verify_signature(stored,old['signature'],old['publisher_key'])
                            stored_fp=sha256(base64.b64decode(old['publisher_key'],validate=True)).hexdigest()
                            if not hmac.compare_digest(str(old['publisher_fingerprint']).lower(),stored_fp): raise ValueError('stored fingerprint mismatch')
                    except Exception: return self._json(500,{'error':'existing immutable version integrity failure'})
                    if stored_digest==digest and old.get('publisher_fingerprint','')==meta.get('publisher_fingerprint',''):
                        return self._json(200,{**old,'idempotent':True})
                    return self._json(409,{'error':'immutable package version already exists'})
                stage=Path(tempfile.mkdtemp(prefix=f'.{ver}.publish-',dir=parent))
                try:
                    (stage/'package.sagapkg').write_bytes(data); (stage/'metadata.json').write_text(json.dumps(meta,indent=2,sort_keys=True)+'\n',encoding='utf-8'); os.replace(stage,d)
                    _index_upsert(root, meta, d/'metadata.json')
                except Exception:
                    shutil.rmtree(stage,ignore_errors=True); return self._json(500,{'error':'failed to persist package'})
            return self._json(201,meta)
    class Server(ThreadingHTTPServer):
        daemon_threads=True; allow_reuse_address=True
    return Server((host,port),H)

def publish(project:str|Path,registry:str,token:str='',signing_key:str|Path|None=None)->dict:
    registry=_validate_registry_url(registry)
    project=Path(project).resolve(); build_lock(project); pkg=pack_project(project); import tomllib
    d=tomllib.loads((project/'saga.toml').read_text()); name=_safe_name(d['project']['name']); version=_safe_version(d['project']['version']); data=pkg.read_bytes()
    if len(data) > REGISTRY_MAX_PACKAGE_BYTES: raise ValueError('package exceeds registry size limit')
    digest=sha256(data).hexdigest()
    headers={'Content-Type':'application/vnd.saga.package','X-Saga-Sha256':digest,**({'Authorization':'Bearer '+token} if token else {})}
    try:
        from .capability_audit import audit
        caps=audit(project/d['project'].get('entry','main.saga')).get('capabilities',[])
        if caps: headers['X-Saga-Capabilities']=','.join(caps)
    except Exception: pass
    if signing_key:
        sig,pub,fp=_sign(data,signing_key); headers.update({'X-Saga-Signature':sig,'X-Saga-Publisher-Key':pub,'X-Saga-Publisher-Fingerprint':fp})
    req=Request(registry.rstrip('/')+f'/v1/packages/{quote(name)}/{quote(version)}',data=data,method='PUT',headers=headers)
    with urlopen(req,timeout=30) as r: return json.loads(_read_limited(r, REGISTRY_MAX_METADATA_BYTES))

def search(registry:str,q:str)->list[dict]:
    registry=_validate_registry_url(registry)
    with urlopen(registry.rstrip('/')+'/v1/search?q='+quote(q),timeout=15) as r: return json.loads(_read_limited(r, REGISTRY_MAX_METADATA_BYTES)).get('packages',[])

def _validate_installed_identity(target: Path, expected_name: str, expected_version: str, expected_archive_sha256: str = '') -> None:
    """Validate extracted metadata and, when supplied, its exact canonical artifact digest."""
    import tomllib
    manifest = target / 'saga.toml'
    lock = target / 'saga.lock'
    if not manifest.is_file() or not lock.is_file():
        raise ValueError('package is missing saga.toml or saga.lock')
    try:
        project_doc = tomllib.loads(manifest.read_text(encoding='utf-8')).get('project', {})
    except Exception as exc:
        raise ValueError('package saga.toml is malformed') from exc
    if project_doc.get('name') != expected_name or project_doc.get('version') != expected_version:
        raise ValueError('registry package identity does not match requested name/version')
    try:
        lock_doc = _strict_json_loads(lock.read_text(encoding='utf-8'))
    except Exception as exc:
        raise ValueError('package saga.lock is malformed') from exc
    locked = lock_doc.get('project', {}) if isinstance(lock_doc, dict) else {}
    if locked.get('name') != expected_name or locked.get('version') != expected_version:
        raise ValueError('package lock identity does not match requested name/version')
    valid, errors = verify_lock(target)
    if not valid:
        raise ValueError('installed package lock verification failed: ' + '; '.join(errors))
    if expected_archive_sha256:
        from .package_integrity import canonical_archive_sha256, load_and_verify_extracted_lock
        verified_lock, lock_raw, _ = load_and_verify_extracted_lock(
            target, expected_name=expected_name, expected_version=expected_version
        )
        actual_archive = canonical_archive_sha256(target, verified_lock, lock_raw)
        if not hmac.compare_digest(actual_archive, str(expected_archive_sha256).strip().lower()):
            raise ValueError('installed package no longer matches the downloaded registry artifact')


def install(registry:str,spec:str,project:str|Path='.',trust_once:str='',allow_unsigned:bool=False) -> Path:
    registry=_validate_registry_url(registry)
    if '@' not in spec: raise ValueError('package spec must be name@version')
    name,version=spec.rsplit('@',1); name=_safe_name(name); version=_safe_version(version)
    root=Path(project).resolve(); target=root/'vendor'/name/version
    req=Request(registry.rstrip('/')+f'/v1/packages/{quote(name)}/{quote(version)}')
    with urlopen(req,timeout=30) as r:
        data=_read_limited(r, REGISTRY_MAX_PACKAGE_BYTES); expected=r.headers.get('X-Saga-Sha256'); sig=r.headers.get('X-Saga-Signature'); pub=r.headers.get('X-Saga-Publisher-Key'); fp=r.headers.get('X-Saga-Publisher-Fingerprint')
    actual=sha256(data).hexdigest()
    if expected and expected!=actual: raise ValueError('registry package hash mismatch')
    archive_name,archive_version=_archive_identity(data)
    if archive_name!=name or archive_version!=version: raise ValueError('registry returned mismatched package identity')
    actual_fp = ''
    if sig or pub or fp:
        if not (sig and pub and fp): raise ValueError('incomplete publisher signature metadata')
        try: _verify_signature(data,sig,pub)
        except Exception as exc: raise ValueError('registry package signature verification failed') from exc
        actual_fp=sha256(base64.b64decode(pub,validate=True)).hexdigest()
        if not hmac.compare_digest(fp.lower(),actual_fp): raise ValueError('registry publisher fingerprint mismatch')
        requested=str(trust_once).strip().lower()
        if requested:
            if not hmac.compare_digest(requested,actual_fp): raise ValueError('publisher fingerprint does not match trust decision')
            trust_fingerprint(root,actual_fp)
        elif actual_fp not in _load_trust_store(root):
            raise ValueError(f'untrusted publisher {actual_fp}; review the publisher key and retry with trust_once or add it to saga.trust.json')
    elif not allow_unsigned:
        raise ValueError('unsigned registry package rejected; signed package is required')
    target_parent=target.parent
    # Do not stage archives through user-controlled directory links.
    current=root
    for component in ('vendor',name,version):
        current=current/component
        if current.is_symlink():
            raise ValueError(f'package destination contains a symbolic link: {current}')
        try:
            current.resolve().relative_to(root)
        except ValueError as exc:
            raise ValueError('package destination escapes project') from exc
    target_parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix=f'.{version}.install-',dir=target_parent))
    try:
        staging_resolved = staging.resolve()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos=z.infolist()
            if len(infos) > REGISTRY_MAX_EXTRACTED_FILES: raise ValueError('package contains too many files')
            total=0; seen=set()
            for info in infos:
                rel=_portable_zip_path(info.filename.rstrip('/') or info.filename)
                if rel in seen: raise ValueError('duplicate package path')
                seen.add(rel)
                total += info.file_size
                if info.file_size > REGISTRY_MAX_PACKAGE_BYTES or total > REGISTRY_MAX_EXTRACTED_BYTES: raise ValueError('package expanded content exceeds safety limit')
                mode=(info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode): raise ValueError('package symlinks are not allowed')
                dest=(staging/rel).resolve()
                try: dest.relative_to(staging_resolved)
                except ValueError: raise ValueError('unsafe package path')
                if info.is_dir(): dest.mkdir(parents=True,exist_ok=True); continue
                dest.parent.mkdir(parents=True,exist_ok=True)
                content=z.read(info)
                if len(content) != info.file_size: raise ValueError('package file size mismatch')
                dest.write_bytes(content)
        _validate_installed_identity(staging,name,version,actual)

        # Commit the package directory and dependency manifest as one serialized
        # project-level transaction. This prevents concurrent `saga add` calls
        # from losing one another's dependency records.
        dep_lock=root/'saga.dependencies.json'
        commit_guard=root/'.saga'/'locks'/'package-manager.lock'
        with exclusive_file_lock(commit_guard):
            current={}
            if dep_lock.exists():
                try: current=_strict_json_loads(dep_lock.read_text(encoding='utf-8'))
                except Exception as exc: raise ValueError('existing dependency lock is malformed') from exc
                if not isinstance(current,dict): raise ValueError('existing dependency lock is malformed')
            rec=current.get('packages',{}).get(name,{}) if isinstance(current.get('packages',{}),dict) else {}
            if target.exists():
                try: _validate_installed_identity(target,name,version,actual); target_valid=True
                except Exception: target_valid=False
                if target_valid and rec.get('version')==version and rec.get('sha256')==actual:
                    shutil.rmtree(staging,ignore_errors=True); staging=None
                    return target
                raise ValueError('package target already exists with different or unverifiable contents')

            os.replace(staging,target); staging=None
            try:
                packages=current.setdefault('packages',{})
                if not isinstance(packages,dict): raise ValueError('existing dependency lock packages must be an object')
                packages[name]={'version':version,'sha256':actual,'path':str(target.relative_to(root)),**({'publisher_fingerprint':actual_fp} if sig and pub else {})}
                fd,tmp_name=tempfile.mkstemp(prefix='.'+dep_lock.name+'.',dir=dep_lock.parent)
                tmp=Path(tmp_name)
                try:
                    with os.fdopen(fd,'w',encoding='utf-8',closefd=False) as f:
                        f.write(json.dumps(current,ensure_ascii=False,indent=2,sort_keys=True)+'\n'); f.flush(); os.fsync(f.fileno())
                    os.close(fd); fd=-1
                    os.replace(tmp,dep_lock)
                finally:
                    if fd>=0: os.close(fd)
                    tmp.unlink(missing_ok=True)
            except Exception:
                shutil.rmtree(target,ignore_errors=True)
                raise
        return target
    except Exception:
        if staging is not None: shutil.rmtree(staging, ignore_errors=True)
        raise

