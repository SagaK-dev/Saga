//go:build sagadesktop && cgo

package main

/*
#cgo linux LDFLAGS: -l:libSDL2-2.0.so.0 -lm -ldl
#cgo windows LDFLAGS: -lSDL2
#cgo darwin LDFLAGS: -lSDL2
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#ifdef __linux__
#include <dlfcn.h>
#endif

typedef uint8_t Uint8;
typedef uint16_t Uint16;
typedef int16_t Sint16;
typedef uint32_t Uint32;
typedef uint32_t SDL_AudioDeviceID;
typedef uint16_t SDL_AudioFormat;
typedef struct SDL_Window SDL_Window;
typedef struct _SDL_GameController SDL_GameController;
typedef struct _SDL_Joystick SDL_Joystick;
typedef void* SDL_GLContext;
typedef union SDL_Event { Uint32 type; Uint8 padding[56]; } SDL_Event;
typedef struct SDL_AudioSpec {
    int freq;
    SDL_AudioFormat format;
    Uint8 channels;
    Uint8 silence;
    Uint16 samples;
    Uint16 padding;
    Uint32 size;
    void (*callback)(void*, Uint8*, int);
    void *userdata;
} SDL_AudioSpec;

extern int SDL_Init(Uint32 flags);
extern int SDL_InitSubSystem(Uint32 flags);
extern Uint32 SDL_WasInit(Uint32 flags);
extern const char* SDL_GetError(void);
extern const char* SDL_GetCurrentVideoDriver(void);
extern SDL_Window* SDL_CreateWindow(const char*, int, int, int, int, Uint32);
extern void SDL_DestroyWindow(SDL_Window*);
extern int SDL_PollEvent(SDL_Event*);
extern void SDL_PumpEvents(void);
extern const Uint8* SDL_GetKeyboardState(int*);
extern int SDL_GetScancodeFromName(const char*);
extern Uint32 SDL_GetMouseState(int*,int*);
extern int SDL_NumJoysticks(void);
extern int SDL_IsGameController(int);
extern SDL_GameController* SDL_GameControllerOpen(int);
extern void SDL_GameControllerClose(SDL_GameController*);
extern Uint8 SDL_GameControllerGetButton(SDL_GameController*, int);
extern Sint16 SDL_GameControllerGetAxis(SDL_GameController*, int);
extern SDL_Joystick* SDL_GameControllerGetJoystick(SDL_GameController*);
extern int SDL_JoystickAttachVirtual(int,int,int,int);
extern int SDL_JoystickDetachVirtual(int);
extern int SDL_JoystickSetVirtualButton(SDL_Joystick*,int,Uint8);
extern int SDL_JoystickSetVirtualAxis(SDL_Joystick*,int,Sint16);
extern void SDL_JoystickUpdate(void);
extern SDL_GLContext SDL_GL_CreateContext(SDL_Window*);
extern void SDL_GL_DeleteContext(SDL_GLContext);
extern int SDL_GL_MakeCurrent(SDL_Window*, SDL_GLContext);
extern int SDL_GL_SetSwapInterval(int);
extern void SDL_GL_SwapWindow(SDL_Window*);
extern void* SDL_GL_GetProcAddress(const char*);
extern void SDL_GetWindowSize(SDL_Window*,int*,int*);
extern SDL_AudioDeviceID SDL_OpenAudioDevice(const char*,int,const SDL_AudioSpec*,SDL_AudioSpec*,int);
extern void SDL_CloseAudioDevice(SDL_AudioDeviceID);
extern int SDL_QueueAudio(SDL_AudioDeviceID,const void*,Uint32);
extern void SDL_ClearQueuedAudio(SDL_AudioDeviceID);
extern void SDL_PauseAudioDevice(SDL_AudioDeviceID,int);

#define SDL_INIT_AUDIO 0x00000010u
#define SDL_INIT_VIDEO 0x00000020u
#define SDL_INIT_JOYSTICK 0x00000200u
#define SDL_INIT_GAMECONTROLLER 0x00002000u
#define SDL_WINDOWPOS_CENTERED 0x2FFF0000u
#define SDL_WINDOW_OPENGL 0x00000002u
#define SDL_WINDOW_SHOWN 0x00000004u
#define SDL_WINDOW_RESIZABLE 0x00000020u
#define SDL_QUIT 0x100u
#define AUDIO_S16LSB 0x8010u

#define GL_TEXTURE_2D 0x0DE1u
#define GL_RGBA 0x1908u
#define GL_UNSIGNED_BYTE 0x1401u
#define GL_TEXTURE_MIN_FILTER 0x2801u
#define GL_TEXTURE_MAG_FILTER 0x2800u
#define GL_NEAREST 0x2600u
#define GL_COLOR_BUFFER_BIT 0x00004000u
#define GL_QUADS 0x0007u
#define GL_VERTEX_SHADER 0x8B31u
#define GL_FRAGMENT_SHADER 0x8B30u
#define GL_COMPILE_STATUS 0x8B81u
#define GL_LINK_STATUS 0x8B82u
#define GL_INFO_LOG_LENGTH 0x8B84u
#define GL_VENDOR 0x1F00u
#define GL_RENDERER 0x1F01u
#define GL_VERSION 0x1F02u
#define GL_SHADING_LANGUAGE_VERSION 0x8B8Cu
#define GL_UNPACK_ALIGNMENT 0x0CF5u

typedef const unsigned char* (*PFN_glGetString)(unsigned int);
typedef void (*PFN_glViewport)(int,int,int,int);
typedef void (*PFN_glClearColor)(float,float,float,float);
typedef void (*PFN_glClear)(unsigned int);
typedef void (*PFN_glGenTextures)(int,unsigned int*);
typedef void (*PFN_glDeleteTextures)(int,const unsigned int*);
typedef void (*PFN_glBindTexture)(unsigned int,unsigned int);
typedef void (*PFN_glTexParameteri)(unsigned int,unsigned int,int);
typedef void (*PFN_glTexImage2D)(unsigned int,int,int,int,int,int,unsigned int,unsigned int,const void*);
typedef void (*PFN_glPixelStorei)(unsigned int,int);
typedef void (*PFN_glEnable)(unsigned int);
typedef void (*PFN_glBegin)(unsigned int);
typedef void (*PFN_glEnd)(void);
typedef void (*PFN_glTexCoord2f)(float,float);
typedef void (*PFN_glVertex2f)(float,float);
typedef unsigned int (*PFN_glCreateShader)(unsigned int);
typedef void (*PFN_glShaderSource)(unsigned int,int,const char* const*,const int*);
typedef void (*PFN_glCompileShader)(unsigned int);
typedef void (*PFN_glGetShaderiv)(unsigned int,unsigned int,int*);
typedef void (*PFN_glGetShaderInfoLog)(unsigned int,int,int*,char*);
typedef void (*PFN_glDeleteShader)(unsigned int);
typedef unsigned int (*PFN_glCreateProgram)(void);
typedef void (*PFN_glAttachShader)(unsigned int,unsigned int);
typedef void (*PFN_glLinkProgram)(unsigned int);
typedef void (*PFN_glGetProgramiv)(unsigned int,unsigned int,int*);
typedef void (*PFN_glGetProgramInfoLog)(unsigned int,int,int*,char*);
typedef void (*PFN_glUseProgram)(unsigned int);
typedef int (*PFN_glGetUniformLocation)(unsigned int,const char*);
typedef void (*PFN_glUniform1i)(int,int);
typedef void (*PFN_glDeleteProgram)(unsigned int);

typedef struct SagaGL {
    PFN_glGetString GetString; PFN_glViewport Viewport; PFN_glClearColor ClearColor; PFN_glClear Clear;
    PFN_glGenTextures GenTextures; PFN_glDeleteTextures DeleteTextures; PFN_glBindTexture BindTexture;
    PFN_glTexParameteri TexParameteri; PFN_glTexImage2D TexImage2D; PFN_glPixelStorei PixelStorei;
    PFN_glEnable Enable; PFN_glBegin Begin; PFN_glEnd End; PFN_glTexCoord2f TexCoord2f; PFN_glVertex2f Vertex2f;
    PFN_glCreateShader CreateShader; PFN_glShaderSource ShaderSource; PFN_glCompileShader CompileShader;
    PFN_glGetShaderiv GetShaderiv; PFN_glGetShaderInfoLog GetShaderInfoLog; PFN_glDeleteShader DeleteShader;
    PFN_glCreateProgram CreateProgram; PFN_glAttachShader AttachShader; PFN_glLinkProgram LinkProgram;
    PFN_glGetProgramiv GetProgramiv; PFN_glGetProgramInfoLog GetProgramInfoLog; PFN_glUseProgram UseProgram;
    PFN_glGetUniformLocation GetUniformLocation; PFN_glUniform1i Uniform1i; PFN_glDeleteProgram DeleteProgram;
} SagaGL;

typedef struct SagaRenderer {
    SDL_Window *window; SDL_GLContext context; SagaGL gl; unsigned int texture; unsigned int defaultProgram;
} SagaRenderer;

static char saga_err[4096];
static void saga_seterr(const char* s){ if(!s)s="unknown error"; strncpy(saga_err,s,sizeof(saga_err)-1); saga_err[sizeof(saga_err)-1]=0; }
static const char* saga_last_error(void){ return saga_err; }
static void* saga_proc(const char* n){ void* p=SDL_GL_GetProcAddress(n); if(!p){ saga_seterr(n); } return p; }
#define LOAD(g,field,name) do { (g)->field=(PFN_##name)saga_proc(#name); if(!(g)->field)return 0; } while(0)

static int saga_sdl_init(void){
    Uint32 flags=SDL_INIT_VIDEO|SDL_INIT_JOYSTICK|SDL_INIT_GAMECONTROLLER;
    if((SDL_WasInit(flags)&flags)==flags) return 1;
    if(SDL_Init(flags)!=0){ saga_seterr(SDL_GetError()); return 0; }
    return 1;
}
static SDL_Window* saga_window_open(const char* title,int w,int h){
    if(!saga_sdl_init())return NULL;
    SDL_Window* win=SDL_CreateWindow(title,(int)SDL_WINDOWPOS_CENTERED,(int)SDL_WINDOWPOS_CENTERED,w,h,SDL_WINDOW_OPENGL|SDL_WINDOW_SHOWN|SDL_WINDOW_RESIZABLE);
    if(!win)saga_seterr(SDL_GetError()); return win;
}
static int saga_poll_quit(void){ SDL_Event ev; int q=0; while(SDL_PollEvent(&ev)){ if(ev.type==SDL_QUIT)q=1; } return q; }
static int saga_key_down(const char* name){ SDL_PumpEvents(); int n=0; const Uint8* state=SDL_GetKeyboardState(&n); int sc=SDL_GetScancodeFromName(name); if(sc<0||sc>=n)return 0; return state[sc]?1:0; }
static Uint32 saga_mouse(int* x,int* y){ SDL_PumpEvents(); return SDL_GetMouseState(x,y); }
static SDL_GameController* saga_gamepad_open(int index){ if(!saga_sdl_init())return NULL; if(index<0||index>=SDL_NumJoysticks()||!SDL_IsGameController(index)){saga_seterr("game controller unavailable");return NULL;} SDL_GameController* g=SDL_GameControllerOpen(index);if(!g)saga_seterr(SDL_GetError());return g; }
// Validation helpers use SDL virtual joystick facilities. They exercise the production GameController path.
// A virtual device is never reported as physical-hardware validation.
static int saga_test_virtual_gamepad_attach(void){ if(!saga_sdl_init())return -1; int idx=SDL_JoystickAttachVirtual(1,6,15,0); if(idx<0)saga_seterr(SDL_GetError()); return idx; }
static int saga_test_virtual_gamepad_button(SDL_GameController* g,int button,int down){ if(!g)return -1; SDL_Joystick* j=SDL_GameControllerGetJoystick(g); if(!j)return -1; int rc=SDL_JoystickSetVirtualButton(j,button,down?1:0); SDL_JoystickUpdate(); if(rc!=0)saga_seterr(SDL_GetError()); return rc; }
static int saga_test_virtual_gamepad_axis(SDL_GameController* g,int axis,Sint16 value){ if(!g)return -1; SDL_Joystick* j=SDL_GameControllerGetJoystick(g); if(!j)return -1; int rc=SDL_JoystickSetVirtualAxis(j,axis,value); SDL_JoystickUpdate(); if(rc!=0)saga_seterr(SDL_GetError()); return rc; }
static int saga_test_virtual_gamepad_detach(int idx){ int rc=SDL_JoystickDetachVirtual(idx); if(rc!=0)saga_seterr(SDL_GetError()); return rc; }

static unsigned int saga_compile_shader(SagaRenderer* r,unsigned int kind,const char* src){
    unsigned int sh=r->gl.CreateShader(kind); r->gl.ShaderSource(sh,1,&src,NULL); r->gl.CompileShader(sh); int ok=0;r->gl.GetShaderiv(sh,GL_COMPILE_STATUS,&ok);
    if(!ok){int n=0;r->gl.GetShaderiv(sh,GL_INFO_LOG_LENGTH,&n);if(n<1)n=1;if(n>4000)n=4000;char* b=(char*)calloc((size_t)n+1,1);r->gl.GetShaderInfoLog(sh,n,NULL,b);saga_seterr(b);free(b);r->gl.DeleteShader(sh);return 0;} return sh;
}
static unsigned int saga_program_full(SagaRenderer* r,const char* vert,const char* frag){
    unsigned int vs=saga_compile_shader(r,GL_VERTEX_SHADER,vert);if(!vs)return 0;unsigned int fs=saga_compile_shader(r,GL_FRAGMENT_SHADER,frag);if(!fs){r->gl.DeleteShader(vs);return 0;}
    unsigned int p=r->gl.CreateProgram();r->gl.AttachShader(p,vs);r->gl.AttachShader(p,fs);r->gl.LinkProgram(p);r->gl.DeleteShader(vs);r->gl.DeleteShader(fs);int ok=0;r->gl.GetProgramiv(p,GL_LINK_STATUS,&ok);
    if(!ok){int n=0;r->gl.GetProgramiv(p,GL_INFO_LOG_LENGTH,&n);if(n<1)n=1;if(n>4000)n=4000;char* b=(char*)calloc((size_t)n+1,1);r->gl.GetProgramInfoLog(p,n,NULL,b);saga_seterr(b);free(b);r->gl.DeleteProgram(p);return 0;} return p;
}
static unsigned int saga_program(SagaRenderer* r,const char* frag){
    const char* vert="#version 120\nvarying vec2 v_uv;\nvoid main(){ gl_Position=gl_Vertex; v_uv=gl_MultiTexCoord0.xy; }\n";
    return saga_program_full(r,vert,frag);
}
static SagaRenderer* saga_renderer_create(SDL_Window* w){
    SagaRenderer* r=(SagaRenderer*)calloc(1,sizeof(SagaRenderer));if(!r)return NULL;r->window=w;r->context=SDL_GL_CreateContext(w);if(!r->context){saga_seterr(SDL_GetError());free(r);return NULL;} SDL_GL_MakeCurrent(w,r->context);SDL_GL_SetSwapInterval(1);
    LOAD(&r->gl,GetString,glGetString);LOAD(&r->gl,Viewport,glViewport);LOAD(&r->gl,ClearColor,glClearColor);LOAD(&r->gl,Clear,glClear);LOAD(&r->gl,GenTextures,glGenTextures);LOAD(&r->gl,DeleteTextures,glDeleteTextures);LOAD(&r->gl,BindTexture,glBindTexture);LOAD(&r->gl,TexParameteri,glTexParameteri);LOAD(&r->gl,TexImage2D,glTexImage2D);LOAD(&r->gl,PixelStorei,glPixelStorei);LOAD(&r->gl,Enable,glEnable);LOAD(&r->gl,Begin,glBegin);LOAD(&r->gl,End,glEnd);LOAD(&r->gl,TexCoord2f,glTexCoord2f);LOAD(&r->gl,Vertex2f,glVertex2f);LOAD(&r->gl,CreateShader,glCreateShader);LOAD(&r->gl,ShaderSource,glShaderSource);LOAD(&r->gl,CompileShader,glCompileShader);LOAD(&r->gl,GetShaderiv,glGetShaderiv);LOAD(&r->gl,GetShaderInfoLog,glGetShaderInfoLog);LOAD(&r->gl,DeleteShader,glDeleteShader);LOAD(&r->gl,CreateProgram,glCreateProgram);LOAD(&r->gl,AttachShader,glAttachShader);LOAD(&r->gl,LinkProgram,glLinkProgram);LOAD(&r->gl,GetProgramiv,glGetProgramiv);LOAD(&r->gl,GetProgramInfoLog,glGetProgramInfoLog);LOAD(&r->gl,UseProgram,glUseProgram);LOAD(&r->gl,GetUniformLocation,glGetUniformLocation);LOAD(&r->gl,Uniform1i,glUniform1i);LOAD(&r->gl,DeleteProgram,glDeleteProgram);
    r->gl.GenTextures(1,&r->texture);r->gl.BindTexture(GL_TEXTURE_2D,r->texture);r->gl.TexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_NEAREST);r->gl.TexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_NEAREST);r->gl.PixelStorei(GL_UNPACK_ALIGNMENT,1);
    const char* frag="#version 120\nuniform sampler2D u_tex;\nvarying vec2 v_uv;\nvoid main(){ gl_FragColor=texture2D(u_tex,v_uv); }\n";r->defaultProgram=saga_program(r,frag);if(!r->defaultProgram){r->gl.DeleteTextures(1,&r->texture);SDL_GL_DeleteContext(r->context);free(r);return NULL;}return r;
}
static void saga_renderer_destroy(SagaRenderer* r){if(!r)return;SDL_GL_MakeCurrent(r->window,r->context);if(r->defaultProgram)r->gl.DeleteProgram(r->defaultProgram);if(r->texture)r->gl.DeleteTextures(1,&r->texture);SDL_GL_DeleteContext(r->context);free(r);}
static unsigned int saga_shader_create(SagaRenderer* r,const char* fragment){if(!r)return 0;SDL_GL_MakeCurrent(r->window,r->context);return saga_program(r,fragment);}
static unsigned int saga_shader_create_full(SagaRenderer* r,const char* vertex,const char* fragment){if(!r)return 0;SDL_GL_MakeCurrent(r->window,r->context);return saga_program_full(r,vertex,fragment);}
static void saga_shader_destroy(SagaRenderer* r,unsigned int p){if(r&&p){SDL_GL_MakeCurrent(r->window,r->context);r->gl.DeleteProgram(p);}}
static int saga_present(SagaRenderer* r,const unsigned char* rgba,int w,int h,unsigned int shader){
    if(!r||!rgba||w<=0||h<=0)return 0;if(SDL_GL_MakeCurrent(r->window,r->context)!=0){saga_seterr(SDL_GetError());return 0;}int ww=0,wh=0;SDL_GetWindowSize(r->window,&ww,&wh);r->gl.Viewport(0,0,ww,wh);r->gl.ClearColor(0,0,0,1);r->gl.Clear(GL_COLOR_BUFFER_BIT);r->gl.BindTexture(GL_TEXTURE_2D,r->texture);r->gl.TexImage2D(GL_TEXTURE_2D,0,GL_RGBA,w,h,0,GL_RGBA,GL_UNSIGNED_BYTE,rgba);r->gl.Enable(GL_TEXTURE_2D);unsigned int p=shader?shader:r->defaultProgram;r->gl.UseProgram(p);int loc=r->gl.GetUniformLocation(p,"u_tex");if(loc>=0)r->gl.Uniform1i(loc,0);r->gl.Begin(GL_QUADS);r->gl.TexCoord2f(0,1);r->gl.Vertex2f(-1,-1);r->gl.TexCoord2f(1,1);r->gl.Vertex2f(1,-1);r->gl.TexCoord2f(1,0);r->gl.Vertex2f(1,1);r->gl.TexCoord2f(0,0);r->gl.Vertex2f(-1,1);r->gl.End();r->gl.UseProgram(0);SDL_GL_SwapWindow(r->window);return 1;
}
static const char* saga_renderer_info(SagaRenderer* r){ static char info[1536];if(!r)return "";SDL_GL_MakeCurrent(r->window,r->context);const unsigned char* vendor=r->gl.GetString(GL_VENDOR);const unsigned char* renderer=r->gl.GetString(GL_RENDERER);const unsigned char* gl=r->gl.GetString(GL_VERSION);const unsigned char* sl=r->gl.GetString(GL_SHADING_LANGUAGE_VERSION);const char* vd=SDL_GetCurrentVideoDriver();snprintf(info,sizeof(info),"SDL2/%s vendor=%s renderer=%s OpenGL=%s GLSL=%s",vd?vd:"?",vendor?(const char*)vendor:"?",renderer?(const char*)renderer:"?",gl?(const char*)gl:"?",sl?(const char*)sl:"?");return info; }



typedef struct SDL_Renderer SDL_Renderer;
typedef struct SDL_Texture SDL_Texture;
typedef struct SDL_RendererInfo {
    const char *name;
    Uint32 flags;
    Uint32 num_texture_formats;
    Uint32 texture_formats[16];
    int max_texture_width;
    int max_texture_height;
} SDL_RendererInfo;
extern int SDL_GetNumRenderDrivers(void);
extern int SDL_GetRenderDriverInfo(int, SDL_RendererInfo*);
extern SDL_Renderer* SDL_CreateRenderer(SDL_Window*, int, Uint32);
extern void SDL_DestroyRenderer(SDL_Renderer*);
extern int SDL_GetRendererInfo(SDL_Renderer*, SDL_RendererInfo*);
extern SDL_Texture* SDL_CreateTexture(SDL_Renderer*, Uint32, int, int, int);
extern void SDL_DestroyTexture(SDL_Texture*);
extern int SDL_UpdateTexture(SDL_Texture*, const void*, const void*, int);
extern int SDL_SetRenderDrawColor(SDL_Renderer*, Uint8, Uint8, Uint8, Uint8);
extern int SDL_RenderClear(SDL_Renderer*);
extern int SDL_RenderCopy(SDL_Renderer*, SDL_Texture*, const void*, const void*);
extern void SDL_RenderPresent(SDL_Renderer*);

#define SDL_RENDERER_SOFTWARE 0x00000001u
#define SDL_RENDERER_ACCELERATED 0x00000002u
#define SDL_TEXTUREACCESS_STREAMING 1
#define SDL_PIXELFORMAT_ABGR8888 0x16762004u

typedef struct SagaRenderer2D {
    SDL_Renderer* renderer;
    SDL_Texture* texture;
    int texture_w;
    int texture_h;
    char info[256];
} SagaRenderer2D;

static int saga_sdl_renderer_index(const char* requested) {
    if(!requested || !requested[0] || strcmp(requested,"native2")==0 || strcmp(requested,"sdl")==0 || strcmp(requested,"auto")==0) return -1;
    int n=SDL_GetNumRenderDrivers();
    for(int i=0;i<n;i++){
        SDL_RendererInfo info; memset(&info,0,sizeof(info));
        if(SDL_GetRenderDriverInfo(i,&info)==0 && info.name && strcmp(info.name,requested)==0) return i;
    }
    return -2;
}
static void saga_sdl_renderer_list(char* out,size_t cap){
    if(!out||cap==0)return;out[0]=0;int n=SDL_GetNumRenderDrivers();
    for(int i=0;i<n;i++){SDL_RendererInfo info;memset(&info,0,sizeof(info));if(SDL_GetRenderDriverInfo(i,&info)==0&&info.name){if(out[0])strncat(out,",",cap-strlen(out)-1);strncat(out,info.name,cap-strlen(out)-1);}}
}
static SagaRenderer2D* saga_renderer2d_create(SDL_Window* w,const char* requested){
    if(!w){saga_seterr("window closed");return NULL;}
    int index=saga_sdl_renderer_index(requested);
    if(index==-2){char names[384];saga_sdl_renderer_list(names,sizeof(names));char msg[512];snprintf(msg,sizeof(msg),"SDL renderer '%s' unavailable; available=%s",requested?requested:"",names);saga_seterr(msg);return NULL;}
    SagaRenderer2D* r=(SagaRenderer2D*)calloc(1,sizeof(SagaRenderer2D));if(!r){saga_seterr("renderer allocation failed");return NULL;}
    Uint32 flags=(requested && strcmp(requested,"software")==0)?SDL_RENDERER_SOFTWARE:SDL_RENDERER_ACCELERATED;
    r->renderer=SDL_CreateRenderer(w,index,flags);
    if(!r->renderer && flags!=0) r->renderer=SDL_CreateRenderer(w,index,0);
    if(!r->renderer){saga_seterr(SDL_GetError());free(r);return NULL;}
    SDL_RendererInfo info;memset(&info,0,sizeof(info));
    if(SDL_GetRendererInfo(r->renderer,&info)==0 && info.name){snprintf(r->info,sizeof(r->info),"SDL2 renderer=%s accelerated=%s max_texture=%dx%d",info.name,(info.flags&SDL_RENDERER_ACCELERATED)?"true":"false",info.max_texture_width,info.max_texture_height);}else{snprintf(r->info,sizeof(r->info),"SDL2 renderer=unknown");}
    SDL_SetRenderDrawColor(r->renderer,0,0,0,255);
    return r;
}
static void saga_renderer2d_destroy(SagaRenderer2D* r){if(!r)return;if(r->texture)SDL_DestroyTexture(r->texture);if(r->renderer)SDL_DestroyRenderer(r->renderer);free(r);}
static int saga_renderer2d_present(SagaRenderer2D* r,const unsigned char* rgba,int w,int h){
    if(!r||!r->renderer||!rgba||w<=0||h<=0){saga_seterr("invalid SDL2D present arguments");return 0;}
    if(!r->texture || r->texture_w!=w || r->texture_h!=h){if(r->texture)SDL_DestroyTexture(r->texture);r->texture=SDL_CreateTexture(r->renderer,SDL_PIXELFORMAT_ABGR8888,SDL_TEXTUREACCESS_STREAMING,w,h);if(!r->texture){saga_seterr(SDL_GetError());return 0;}r->texture_w=w;r->texture_h=h;}
    if(SDL_UpdateTexture(r->texture,NULL,rgba,w*4)!=0){saga_seterr(SDL_GetError());return 0;}
    SDL_RenderClear(r->renderer);if(SDL_RenderCopy(r->renderer,r->texture,NULL,NULL)!=0){saga_seterr(SDL_GetError());return 0;}SDL_RenderPresent(r->renderer);return 1;
}
static const char* saga_renderer2d_info(SagaRenderer2D* r){return r?r->info:"";}
static void saga_renderer2d_drivers(char* out,size_t cap){saga_sdl_renderer_list(out,cap);}


#ifdef __linux__
typedef void* VkInstance;
typedef void* VkPhysicalDevice;
typedef int32_t VkResult;
typedef struct { uint32_t sType; const void* pNext; const char* pApplicationName; uint32_t applicationVersion; const char* pEngineName; uint32_t engineVersion; uint32_t apiVersion; } SagaVkApplicationInfo;
typedef struct { uint32_t sType; const void* pNext; uint32_t flags; const SagaVkApplicationInfo* pApplicationInfo; uint32_t enabledLayerCount; const char* const* ppEnabledLayerNames; uint32_t enabledExtensionCount; const char* const* ppEnabledExtensionNames; } SagaVkInstanceCreateInfo;
typedef VkResult (*PFN_saga_vkEnumerateInstanceVersion)(uint32_t*);
typedef VkResult (*PFN_saga_vkCreateInstance)(const SagaVkInstanceCreateInfo*,const void*,VkInstance*);
typedef void (*PFN_saga_vkDestroyInstance)(VkInstance,const void*);
typedef VkResult (*PFN_saga_vkEnumeratePhysicalDevices)(VkInstance,uint32_t*,VkPhysicalDevice*);
static int saga_vulkan_probe(char* out,size_t cap){
    void* lib=dlopen("libvulkan.so.1",RTLD_NOW|RTLD_LOCAL);if(!lib){snprintf(out,cap,"unavailable: %s",dlerror());return 0;}
    PFN_saga_vkEnumerateInstanceVersion pver=(PFN_saga_vkEnumerateInstanceVersion)dlsym(lib,"vkEnumerateInstanceVersion");
    PFN_saga_vkCreateInstance pcreate=(PFN_saga_vkCreateInstance)dlsym(lib,"vkCreateInstance");
    PFN_saga_vkDestroyInstance pdestroy=(PFN_saga_vkDestroyInstance)dlsym(lib,"vkDestroyInstance");
    PFN_saga_vkEnumeratePhysicalDevices pdevices=(PFN_saga_vkEnumeratePhysicalDevices)dlsym(lib,"vkEnumeratePhysicalDevices");
    if(!pcreate||!pdestroy||!pdevices){snprintf(out,cap,"unavailable: Vulkan loader missing core entry points");dlclose(lib);return 0;}
    uint32_t loaderVersion=(1u<<22); if(pver) pver(&loaderVersion);
    uint32_t candidates[5]; size_t candidateCount=0; candidates[candidateCount++]=loaderVersion;
    const uint32_t fixedVersions[4]={(1u<<22)|(3u<<12),(1u<<22)|(2u<<12),(1u<<22)|(1u<<12),(1u<<22)};
    for(size_t i=0;i<4;i++){int seen=0;for(size_t j=0;j<candidateCount;j++)if(candidates[j]==fixedVersions[i])seen=1;if(!seen)candidates[candidateCount++]=fixedVersions[i];}
    VkInstance inst=NULL;VkResult rc=-9;uint32_t selectedVersion=0;
    for(size_t i=0;i<candidateCount;i++){
        SagaVkApplicationInfo ai;memset(&ai,0,sizeof(ai));ai.sType=0;ai.pApplicationName="Saga";ai.applicationVersion=16;ai.pEngineName="Saga Native";ai.engineVersion=16;ai.apiVersion=candidates[i];
        SagaVkInstanceCreateInfo ci;memset(&ci,0,sizeof(ci));ci.sType=1;ci.pApplicationInfo=&ai;
        inst=NULL;rc=pcreate(&ci,NULL,&inst);if(rc==0&&inst){selectedVersion=candidates[i];break;}
    }
    if(!inst){snprintf(out,cap,"loader=%u.%u.%u create_instance=%d",loaderVersion>>22,(loaderVersion>>12)&1023,loaderVersion&4095,(int)rc);dlclose(lib);return 0;}
    uint32_t count=0;rc=pdevices(inst,&count,NULL);pdestroy(inst,NULL);dlclose(lib);if(rc!=0){snprintf(out,cap,"loader=%u.%u.%u instance=%u.%u.%u enumerate_devices=%d",loaderVersion>>22,(loaderVersion>>12)&1023,loaderVersion&4095,selectedVersion>>22,(selectedVersion>>12)&1023,selectedVersion&4095,(int)rc);return 0;}
    snprintf(out,cap,"Vulkan loader=%u.%u.%u instance=%u.%u.%u physical_devices=%u",loaderVersion>>22,(loaderVersion>>12)&1023,loaderVersion&4095,selectedVersion>>22,(selectedVersion>>12)&1023,selectedVersion&4095,count);return count>0?1:0;
}
#else
static int saga_vulkan_probe(char* out,size_t cap){snprintf(out,cap,"Vulkan probe not compiled for this host");return 0;}
#endif

static SDL_AudioDeviceID saga_audio_device=0;static int saga_audio_rate=0;static int saga_audio_channels=0;
static int saga_audio_play(const void* pcm,Uint32 n,int rate,int channels){ if(!saga_sdl_init())return 0;if(!(SDL_WasInit(SDL_INIT_AUDIO)&SDL_INIT_AUDIO)){if(SDL_InitSubSystem(SDL_INIT_AUDIO)!=0){saga_seterr(SDL_GetError());return 0;}}if(rate<=0||(channels!=1&&channels!=2)){saga_seterr("invalid audio format");return 0;}if(!saga_audio_device||rate!=saga_audio_rate||channels!=saga_audio_channels){if(saga_audio_device)SDL_CloseAudioDevice(saga_audio_device);SDL_AudioSpec want,have;memset(&want,0,sizeof(want));want.freq=rate;want.format=AUDIO_S16LSB;want.channels=(Uint8)channels;want.samples=1024;saga_audio_device=SDL_OpenAudioDevice(NULL,0,&want,&have,0);if(!saga_audio_device){saga_seterr(SDL_GetError());return 0;}saga_audio_rate=rate;saga_audio_channels=channels;}if(SDL_QueueAudio(saga_audio_device,pcm,n)!=0){saga_seterr(SDL_GetError());return 0;}SDL_PauseAudioDevice(saga_audio_device,0);return 1; }
*/
import "C"

import (
	"fmt"
	"runtime"
	"strings"
	"sync"
	"unsafe"
)

var desktopThreadOnce sync.Once
var desktopThreadJobs chan func()

func desktopOnThread(fn func()) {
	desktopThreadOnce.Do(func() {
		desktopThreadJobs = make(chan func())
		ready := make(chan struct{})
		go func() {
			runtime.LockOSThread()
			close(ready)
			for job := range desktopThreadJobs {
				job()
			}
		}()
		<-ready
	})
	done := make(chan struct{})
	desktopThreadJobs <- func() { defer close(done); fn() }
	<-done
}

func cErr() error                { return fmt.Errorf("%s", C.GoString(C.saga_last_error())) }
func desktopAvailable() bool     { return true }
func desktopBackendName() string { return "SDL2+OpenGL" }
func desktopOpenWindow(title string, w, h int) (out uintptr, err error) {
	desktopOnThread(func() {
		cs := C.CString(title)
		defer C.free(unsafe.Pointer(cs))
		p := C.saga_window_open(cs, C.int(w), C.int(h))
		if p == nil {
			err = cErr()
			return
		}
		out = uintptr(unsafe.Pointer(p))
	})
	return
}
func desktopCloseWindow(h uintptr) {
	if h != 0 {
		desktopOnThread(func() { C.SDL_DestroyWindow((*C.SDL_Window)(unsafe.Pointer(h))) })
	}
}
func desktopPoll(h uintptr) (out bool, err error) {
	if h == 0 {
		return false, fmt.Errorf("window closed")
	}
	desktopOnThread(func() { out = C.saga_poll_quit() != 0 })
	return
}
func desktopKeyDown(h uintptr, key string) (out bool, err error) {
	if h == 0 {
		return false, fmt.Errorf("window closed")
	}
	desktopOnThread(func() { cs := C.CString(key); defer C.free(unsafe.Pointer(cs)); out = C.saga_key_down(cs) != 0 })
	return
}
func desktopMouse(h uintptr) (xout, yout int, bout uint32, err error) {
	if h == 0 {
		return 0, 0, 0, fmt.Errorf("window closed")
	}
	desktopOnThread(func() { var x, y C.int; b := C.saga_mouse(&x, &y); xout = int(x); yout = int(y); bout = uint32(b) })
	return
}

// desktopTestVirtualGamepad* are package-private validation hooks. They are not
// registered as Saga language APIs and never substitute for a physical-device gate.
func desktopTestVirtualGamepadAttach() (out int, err error) {
	desktopOnThread(func() {
		out = int(C.saga_test_virtual_gamepad_attach())
		if out < 0 {
			err = cErr()
		}
	})
	return
}
func desktopTestVirtualGamepadButton(h uintptr, button int, down bool) (err error) {
	if h == 0 {
		return fmt.Errorf("gamepad closed")
	}
	v := 0
	if down {
		v = 1
	}
	desktopOnThread(func() {
		if C.saga_test_virtual_gamepad_button((*C.SDL_GameController)(unsafe.Pointer(h)), C.int(button), C.int(v)) != 0 {
			err = cErr()
		}
	})
	return
}
func desktopTestVirtualGamepadAxis(h uintptr, axis int, value int16) (err error) {
	if h == 0 {
		return fmt.Errorf("gamepad closed")
	}
	desktopOnThread(func() {
		if C.saga_test_virtual_gamepad_axis((*C.SDL_GameController)(unsafe.Pointer(h)), C.int(axis), C.Sint16(value)) != 0 {
			err = cErr()
		}
	})
	return
}
func desktopTestVirtualGamepadDetach(index int) (err error) {
	desktopOnThread(func() {
		if C.saga_test_virtual_gamepad_detach(C.int(index)) != 0 {
			err = cErr()
		}
	})
	return
}

func desktopGamepadCount() (out int) {
	desktopOnThread(func() {
		if C.saga_sdl_init() != 0 {
			out = int(C.SDL_NumJoysticks())
		}
	})
	return
}
func desktopOpenGamepad(index int) (out uintptr, err error) {
	desktopOnThread(func() {
		p := C.saga_gamepad_open(C.int(index))
		if p == nil {
			err = cErr()
			return
		}
		out = uintptr(unsafe.Pointer(p))
	})
	return
}
func desktopCloseGamepad(h uintptr) {
	if h != 0 {
		desktopOnThread(func() { C.SDL_GameControllerClose((*C.SDL_GameController)(unsafe.Pointer(h))) })
	}
}
func desktopGamepadButton(h uintptr, button string) (out bool, err error) {
	if h == 0 {
		return false, fmt.Errorf("gamepad closed")
	}
	m := map[string]int{"a": 0, "b": 1, "x": 2, "y": 3, "back": 4, "guide": 5, "start": 6, "leftstick": 7, "rightstick": 8, "leftshoulder": 9, "rightshoulder": 10, "dpad_up": 11, "dpad_down": 12, "dpad_left": 13, "dpad_right": 14}
	n, ok := m[strings.ToLower(button)]
	if !ok {
		return false, fmt.Errorf("unknown gamepad button %q", button)
	}
	desktopOnThread(func() {
		C.SDL_PumpEvents()
		out = C.SDL_GameControllerGetButton((*C.SDL_GameController)(unsafe.Pointer(h)), C.int(n)) != 0
	})
	return
}
func desktopGamepadAxis(h uintptr, axis string) (out float64, err error) {
	if h == 0 {
		return 0, fmt.Errorf("gamepad closed")
	}
	m := map[string]int{"leftx": 0, "lefty": 1, "rightx": 2, "righty": 3, "triggerleft": 4, "triggerright": 5}
	n, ok := m[strings.ToLower(axis)]
	if !ok {
		return 0, fmt.Errorf("unknown gamepad axis %q", axis)
	}
	desktopOnThread(func() {
		C.SDL_PumpEvents()
		v := int16(C.SDL_GameControllerGetAxis((*C.SDL_GameController)(unsafe.Pointer(h)), C.int(n)))
		if v < 0 {
			out = float64(v) / 32768.0
		} else {
			out = float64(v) / 32767.0
		}
	})
	return
}
func desktopRendererCreate(window uintptr) (out uintptr, info string, err error) {
	if window == 0 {
		return 0, "", fmt.Errorf("window closed")
	}
	desktopOnThread(func() {
		r := C.saga_renderer_create((*C.SDL_Window)(unsafe.Pointer(window)))
		if r == nil {
			err = cErr()
			return
		}
		out = uintptr(unsafe.Pointer(r))
		info = C.GoString(C.saga_renderer_info(r))
	})
	return
}
func desktopRendererDestroy(h uintptr) {
	if h != 0 {
		desktopOnThread(func() { C.saga_renderer_destroy((*C.SagaRenderer)(unsafe.Pointer(h))) })
	}
}
func desktopShaderCreate(renderer uintptr, fragment string) (out uintptr, err error) {
	if renderer == 0 {
		return 0, fmt.Errorf("renderer closed")
	}
	desktopOnThread(func() {
		cs := C.CString(fragment)
		defer C.free(unsafe.Pointer(cs))
		p := C.saga_shader_create((*C.SagaRenderer)(unsafe.Pointer(renderer)), cs)
		if p == 0 {
			err = cErr()
			return
		}
		out = uintptr(p)
	})
	return
}
func desktopShaderProgramCreate(renderer uintptr, vertex, fragment string) (out uintptr, err error) {
	if renderer == 0 {
		return 0, fmt.Errorf("renderer closed")
	}
	desktopOnThread(func() {
		vs := C.CString(vertex)
		fs := C.CString(fragment)
		defer C.free(unsafe.Pointer(vs))
		defer C.free(unsafe.Pointer(fs))
		p := C.saga_shader_create_full((*C.SagaRenderer)(unsafe.Pointer(renderer)), vs, fs)
		if p == 0 {
			err = cErr()
			return
		}
		out = uintptr(p)
	})
	return
}
func desktopShaderDestroy(renderer, shader uintptr) {
	if renderer != 0 && shader != 0 {
		desktopOnThread(func() { C.saga_shader_destroy((*C.SagaRenderer)(unsafe.Pointer(renderer)), C.uint(shader)) })
	}
}
func desktopRendererPresent(renderer uintptr, rgba []byte, w, h int, shader uintptr) (err error) {
	if renderer == 0 {
		return fmt.Errorf("renderer closed")
	}
	if w <= 0 || h <= 0 || len(rgba) < w*h*4 {
		return fmt.Errorf("framebuffer too small")
	}
	if len(rgba) == 0 {
		return fmt.Errorf("empty framebuffer")
	}
	desktopOnThread(func() {
		if C.saga_present((*C.SagaRenderer)(unsafe.Pointer(renderer)), (*C.uchar)(unsafe.Pointer(&rgba[0])), C.int(w), C.int(h), C.uint(shader)) == 0 {
			err = cErr()
		}
	})
	return
}
func desktopVulkanProbe() (ok bool, info string) {
	desktopOnThread(func() {
		buf := make([]byte, 512)
		ok = C.saga_vulkan_probe((*C.char)(unsafe.Pointer(&buf[0])), C.size_t(len(buf))) != 0
		n := 0
		for n < len(buf) && buf[n] != 0 {
			n++
		}
		info = string(buf[:n])
	})
	return
}

func desktopAudioPlay(pcm []byte, rate, channels int) (err error) {
	if len(pcm) == 0 {
		return fmt.Errorf("empty audio clip")
	}
	desktopOnThread(func() {
		if C.saga_audio_play(unsafe.Pointer(&pcm[0]), C.Uint32(len(pcm)), C.int(rate), C.int(channels)) == 0 {
			err = cErr()
		}
	})
	return
}

func desktopRenderer2DCreate(window uintptr, requested string) (out uintptr, info string, err error) {
	if window == 0 {
		return 0, "", fmt.Errorf("window closed")
	}
	desktopOnThread(func() {
		cs := C.CString(strings.ToLower(strings.TrimSpace(requested)))
		defer C.free(unsafe.Pointer(cs))
		r := C.saga_renderer2d_create((*C.SDL_Window)(unsafe.Pointer(window)), cs)
		if r == nil {
			err = cErr()
			return
		}
		out = uintptr(unsafe.Pointer(r))
		info = C.GoString(C.saga_renderer2d_info(r))
	})
	return
}
func desktopRenderer2DDestroy(h uintptr) {
	if h != 0 {
		desktopOnThread(func() { C.saga_renderer2d_destroy((*C.SagaRenderer2D)(unsafe.Pointer(h))) })
	}
}
func desktopRenderer2DPresent(h uintptr, rgba []byte, w, hgt int) (err error) {
	if h == 0 {
		return fmt.Errorf("renderer closed")
	}
	if w <= 0 || hgt <= 0 || len(rgba) < w*hgt*4 {
		return fmt.Errorf("framebuffer too small")
	}
	desktopOnThread(func() {
		if C.saga_renderer2d_present((*C.SagaRenderer2D)(unsafe.Pointer(h)), (*C.uchar)(unsafe.Pointer(&rgba[0])), C.int(w), C.int(hgt)) == 0 {
			err = cErr()
		}
	})
	return
}
func desktopRendererDrivers() (out string) {
	desktopOnThread(func() {
		buf := make([]byte, 1024)
		C.saga_renderer2d_drivers((*C.char)(unsafe.Pointer(&buf[0])), C.size_t(len(buf)))
		n := 0
		for n < len(buf) && buf[n] != 0 {
			n++
		}
		out = string(buf[:n])
	})
	return
}
