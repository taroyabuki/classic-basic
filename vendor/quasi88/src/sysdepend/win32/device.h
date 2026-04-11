#ifndef DEVICE_H_INCLUDED
#define DEVICE_H_INCLUDED


#include <windows.h>
#include <stdio.h>


/*
 * src/sysdepend/win32/ 以下でのグローバル変数
 */
extern HINSTANCE g_hInstance;
extern HWND      g_hWnd;
extern HMENU     g_hMenu;

extern int g_keyrepeat;
extern int g_pcm_bufsize;

extern FILE *debugfp;
#if 1
#define DPRINTF(format, ...)    (fprintf(debugfp, format, ## __VA_ARGS__))
#else
#define DPRINTF(format, ...)    {}
#endif



/*
 * src/sysdepend/win32/ 以下でのグローバル変数 (オプション設定可能な変数)
 */



/*
 * src/sysdepend/win32/ 以下でのグローバル関数
 */
LRESULT CALLBACK WndProc(HWND hWnd, UINT msg, WPARAM wp, LPARAM lp);
extern int  graph_update_WM_PAINT(void);
extern void menubar_setup(int active);
extern int  menubar_event(int id);
extern void menubar_controll(int controll);
extern void wave_event_open(HWAVEOUT hwo);
extern void wave_event_done(HWAVEOUT hwo, LPWAVEHDR lpwhdr);
extern void wave_event_close(HWAVEOUT hwo);


#endif /* DEVICE_H_INCLUDED */
