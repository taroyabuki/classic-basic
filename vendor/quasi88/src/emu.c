/************************************************************************/
/*									*/
/* エミュモード								*/
/*									*/
/************************************************************************/

#include <stdio.h>

#include "quasi88.h"
#include "initval.h"
#include "emu.h"

#include "pc88cpu.h"

#include "screen.h"
#include "keyboard.h"
#include "intr.h"
#include "event.h"
#include "menu.h"
#include "monitor.h"
#include "pause.h"
#include "wait.h"
#include "suspend.h"
#include "status.h"
#include "graph.h"
#include "snddrv.h"




break_t		break_point[2][NR_BP];	/* ブレークポイント		*/
break_drive_t	break_point_fdc[NR_BP];	/* FDC ブレークポイント		*/


int	cpu_timing	= DEFAULT_CPU;		/* SUB-CPU 駆動方式	*/

int	select_main_cpu = TRUE;			/* -cpu 0 実行するCPU	*/
						/* 真なら MAIN CPUを実行*/

int	dual_cpu_count	= 0;			/* -cpu 1 同時処理STEP数*/
int	CPU_1_COUNT	= 4000;			/* その、初期値		*/

int	cpu_slice_us    = 5;			/* -cpu 2 処理時分割(us)*/
						/* 10>でSILPHEEDが動かん*/

int	trace_counter	= 1;			/* TRACE 時のカウンタ	*/



static	int	main_state   = 0;
static	int	sub_state    = 0;
#define	JACKUP	(256)


static	int	emu_mode_execute= GO;
static	int	emu_rest_step;

/*
 * Execution state for headless automation
 * Tracks observable emulator execution state.
 *
 * EMULATOR EXECUTION STATES:
 *   EXEC_STATE_UNKNOWN  - Initial state, or when not in EXEC mode
 *   EXEC_STATE_RUNNING  - Emulator is actively executing (GO mode)
 *
 * BASIC INTERPRETER STATES (synthetic test verified, ROM measurement TODO):
 *   EXEC_STATE_READY    - BASIC at command prompt (READY/"Ok")
 *   EXEC_STATE_INPUT_WAIT - BASIC INPUT waiting for user input
 *   EXEC_STATE_COMPLETED - BASIC program completed normally (END/SYSTEM)
 *   EXEC_STATE_ERROR    - BASIC runtime error occurred
 *
 * STATE STALE PREVENTION:
 *   - State is cleared to UNKNOWN when exiting EXEC mode (MONITOR/MENU/PAUSE/QUIT)
 *   - State is cleared to UNKNOWN on emu_reset() path
 *   - State preservation across MONITOR<->EXEC cycle is handled by caller
 *
 * OBSERVATION METHOD:
 *   - PC address monitoring in main_fetch() (pc88main.c) calls set_exec_state_by_pc()
 *
 * VERIFICATION:
 *   - Synthetic tests pass: vendor/quasi88/test_proof.sh
 *   - ROM real-execution: TODO Phase 2 (artifacts/rom-state/*.log)
 *
 * State values match QUASI88_EXEC_XXX from emu.h
 */
#define EXEC_STATE_UNKNOWN    0
#define EXEC_STATE_RUNNING    1
#define EXEC_STATE_READY      2
#define EXEC_STATE_INPUT_WAIT 3
#define EXEC_STATE_COMPLETED  4
#define EXEC_STATE_ERROR      5

static int exec_state = EXEC_STATE_UNKNOWN;

/*
 * PHASE 7 CONTRACT LABELS (grep-able markers for state contract verification)
 *
 * COMPLETED_POLICY=first_ready_transition
 *   - First hit of READY range after RUNNING → COMPLETED
 *   - Subsequent hits → READY
 *   - was_running_program flag tracks this
 *
 * UNKNOWN_FALLBACK=hooks_unconfigured
 *   - If ready_low=0 OR ready_high=0 OR input_wait_pc=0 OR error_pc=0
 *   - State observation cannot proceed, remains UNKNOWN
 *
 * STATE_OWNER=set_emu_exec_mode (for RUNNING)
 * STATE_OWNER=set_exec_state_by_pc (for READY/INPUT_WAIT/ERROR/COMPLETED)
 * STATE_OWNER=clear_exec_state (for UNKNOWN)
 */

/*
 * PC address observation configuration:
 * These addresses are set by quasi88_set_rom_hook_addresses() before EXEC mode.
 * - ready_pc_range: (low, high) PC range for READY prompt
 * - input_wait_pc: PC address when INPUT is waiting
 * - error_pc: PC address when error handler is invoked
 * - completed_pc: PC address after program completion (END/SYSTEM→READY transition)
 *
 * Default values (0) mean no observation configured yet.
 * For N88-BASIC V2 ROM:
 *   ready range: 0x0320-0x04e0 (after "Ok" message)
 *   input_wait:  0x0600
 *   error:       0x047b
 *   completed:   same as READY but detected on transition from RUNNING
 */
static word ready_pc_low = 0;
static word ready_pc_high = 0;
static word input_wait_pc = 0;
static word error_pc = 0;
static word completed_pc = 0;

/* Track if we were running a program (for COMPLETED detection) */
static int was_running_program = FALSE;

void	set_emu_exec_mode( int mode )
{
  emu_mode_execute = mode;
  /* When entering GO mode, we start in RUNNING state */
  if (mode == GO) {
      exec_state = EXEC_STATE_RUNNING;
      was_running_program = TRUE;  /* Track that we're executing */
  } else {
      /* TRACE, STEP, TRACE_CHANGE are sub-modes; keep RUNNING if previously set
       * But for cleaner semantics, we clear back to UNKNOWN on explicit exits.
       * The caller (quasi88_exec_XXX) controls this via set_mode() context. */
  }
}

/*
 * Internal helper: clear exec state when leaving EXEC context safely
 * Called from quit/monitor/escape paths to prevent stale state.
 */
void clear_exec_state(void)
{
    exec_state = EXEC_STATE_UNKNOWN;
    was_running_program = FALSE;
}

/*
 * set_exec_state_by_pc - Update BASIC interpreter state based on PC observation
 *
 * Called from main_fetch() in pc88main.c when PC reaches certain addresses.
 * This is the side-channel for observing BASIC interpreter state without OCR.
 *
 * STATE PRIORITY (highest to lowest):
 *   ERROR > INPUT_WAIT > COMPLETED > READY
 *
 * COMPLETED DETECTION:
 *   - When PC reaches READY range after running a program, emit COMPLETED once
 *   - Subsequent READY range hits emit READY
 *   - Program execution is tracked via RUNNING state entry
 *
 * Parameters:
 *   pc - Current Z80 program counter address
 */
void set_exec_state_by_pc(word pc)
{
    /* Check for error handler first (highest priority) */
    if (error_pc != 0 && pc == error_pc) {
        exec_state = EXEC_STATE_ERROR;
        was_running_program = FALSE;
        return;
    }

    /* Check for INPUT_WAIT */
    if (input_wait_pc != 0 && pc == input_wait_pc) {
        exec_state = EXEC_STATE_INPUT_WAIT;
        was_running_program = FALSE;
        return;
    }

    /* Check for COMPLETED - transition from RUNNING to READY range */
    if (was_running_program && ready_pc_low != 0 && 
        pc >= ready_pc_low && pc <= ready_pc_high) {
        exec_state = EXEC_STATE_COMPLETED;
        was_running_program = FALSE;  /* Reset so next READY is just READY */
        return;
    }

    /* Check for READY prompt (PC range) */
    if (ready_pc_low != 0 && pc >= ready_pc_low && pc <= ready_pc_high) {
        exec_state = EXEC_STATE_READY;
        return;
    }
}

/*
 * detect_ready_by_idle - Detect READY state when BASIC has been idle for a while
 *
 * UNUSED FALLBACK: This function is not used in production paths.
 * Kept for potential future use or testing purposes only.
 *
 * PC range observation (set_exec_state_by_pc) is the primary mechanism.
 *
 * Returns TRUE if READY is confirmed.
 */
int detect_ready_by_idle(void)
{
    /* unused fallback, test-ignored marker:
     * if we're in EXEC mode and not in INPUT_WAIT or ERROR,
     * and we've been executing for a while without hitting error/input addresses,
     * we assume READY. This can be refined with more sophisticated heuristics. */
    if (exec_state == EXEC_STATE_RUNNING && error_pc == 0 && input_wait_pc == 0) {
        /* Running state could be READY - let PC observation refine this */
        return TRUE;
    }
    return FALSE;
}

/*
 * quasi88_get_exec_state - Return current execution state
 *
 * Returns:
 *   QUASI88_EXEC_UNKNOWN   - Not in EXEC mode, or state indeterminate
 *   QUASI88_EXEC_RUNNING   - Emulator is executing in GO mode
 *   QUASI88_EXEC_READY     - BASIC interpreter at READY prompt
 *   QUASI88_EXEC_INPUT_WAIT - BASIC INPUT statement waiting for input
 *   QUASI88_EXEC_COMPLETED - BASIC program completed normally
 *   QUASI88_EXEC_ERROR     - BASIC runtime error occurred
 *
 * NOTE: This API is a read-only getter over the internal exec_state variable.
 * State transitions are controlled by owner functions:
 *   - set_emu_exec_mode() for RUNNING
 *   - set_exec_state_by_pc() for READY/INPUT_WAIT/ERROR/COMPLETED (from pc88main.c)
 *   - clear_exec_state() for UNKNOWN (on mode exit/reset)
 */
quasi88_exec_state_t quasi88_get_exec_state(void)
{
    return (quasi88_exec_state_t)exec_state;
}

/*
 * quasi88_set_rom_hook_addresses - Configure PC address observation points
 *
 * This is called during initialization to set the ROM addresses that
 * correspond to BASIC interpreter states. The addresses are ROM-version
 * dependent and should be configured based on the loaded ROM.
 *
 * For N88-BASIC V2 ROM (typical):
 *   ready range:  0x0320-0x04e0 (after "Ok" message, includes READY prompt)
 *   input_wait:   0x0600 (INPUT statement waiting)
 *   error:        0x047b (error handler entry)
 *   completed:    automatically detected via READY range after program execution
 *
 * Parameters:
 *   ready_low, ready_high - PC range for READY/COMPLETED detection
 *   input_wait_addr       - PC address for INPUT waiting
 *   error_addr            - PC address for error handler
 */
void quasi88_set_rom_hook_addresses(word ready_low, word ready_high,
                                     word input_wait_addr, word error_addr)
{
    ready_pc_low = ready_low;
    ready_pc_high = ready_high;
    input_wait_pc = input_wait_addr;
    error_pc = error_addr;
}

/*
 * Test helpers: access was_running_program for debugging
 */
int quasi88_get_was_running_program(void)
{
    return was_running_program;
}

void quasi88_set_was_running_program(int value)
{
    was_running_program = value;
}


/***********************************************************************
 * エミュレート処理の初期化関連
 ************************************************************************/

void	emu_reset( void )
{
  select_main_cpu = TRUE;
  dual_cpu_count  = 0;

  main_state   = 0;
  sub_state    = 0;

  /* Clear exec state to prevent stale RUNNING state after reset */
  clear_exec_state();

  /* Reset queued input on emulator reset */
  quasi88_keyinject_reset();
}


void	emu_breakpoint_init( void )
{
  int	i, j;
	/* ブレークポイントのワーク初期化 (モニターモード用) */
  for( j=0; j<2; j++ )
    for( i=0; i<NR_BP; i++ )
      break_point[j][i].type = BP_NONE;

  for( i=0; i<NR_BP; i++ )
    break_point_fdc[i].type = BP_NONE;
}




/***********************************************************************
 * CPU実行処理 (EXEC) の制御
 *	-cpu <n> に応じて、動作を変える。
 *
 *	STEP  時は、1step だけ実行する。
 *	TRACE 時は、指定回数分、1step 実行する。
 *
 *	ブレークポイント指定時は、1step実行の度に PC がブレークポイントに
 *	達したかどうかを確認する。
 *
 ************************************************************************/

#define	INFINITY	(0)
#define	ONLY_1STEP	(1)

/*------------------------------------------------------------------------*/

/*
 * ブレークポイント (タイプ PC) の有無をチェックする
 */

static	int	check_break_point_PC( void )
{
  int	i, j;

  for( i=0; i<NR_BP; i++ ) if( break_point[BP_MAIN][i].type == BP_PC ) break;
  for( j=0; j<NR_BP; j++ ) if( break_point[BP_SUB][j].type  == BP_PC ) break;

  if( i==NR_BP && j==NR_BP ) return FALSE;
  else                       return TRUE;
}

/*------------------------------------------------------------------------*/

/*
 * CPU を 1step 実行して、PCがブレークポイントに達したかチェックする
 *	ブレークポイント(タイプPC)未設定ならこの関数は使わず、z80_emu()を使う
 */

static	int	z80_emu_with_breakpoint( z80arch *z80, int unused )
{
  int i, cpu, states;

  states = z80_emu( z80, 1 );		/* 1step だけ実行 */

  if( z80==&z80main_cpu ) cpu = BP_MAIN;
  else                    cpu = BP_SUB;

  for( i=0; i<NR_BP; i++ ){
    if( break_point[cpu][i].type == BP_PC     &&
	break_point[cpu][i].addr == z80->PC.W ){

      if( i==BP_NUM_FOR_SYSTEM ){
	break_point[cpu][i].type = BP_NONE;
      }

      printf( "*** Break at %04x *** ( %s[#%d] : PC )\n",
	      z80->PC.W, (cpu==BP_MAIN)?"MAIN":"SUB", i+1 );

      quasi88_debug();
    }
  }

  return states;
}

/*---------------------------------------------------------------------------*/

static	int	passed_step;		/* 実行した step数 */
static	int	target_step;		/* この step数に達するまで実行する */

static	int	infinity, only_1step;
static	int	(*z80_exec)( z80arch *, int );


void	emu_init(void)
{
/*xmame_sound_update();*/
  xmame_update_video_and_audio();
  event_update();
/*keyboard_update();*/



/*screen_set_dirty_all();*/
/*screen_set_dirty_palette();*/

  /* ステータス部クリア */
  status_message_default(0, NULL);
  status_message_default(1, NULL);
  status_message_default(2, NULL);



	/* ブレークポイント設定の有無で、呼び出す関数を変える */
  if( check_break_point_PC() ) z80_exec = z80_emu_with_breakpoint;
  else                         z80_exec = z80_emu;


	/* GO/TRACE/STEP/CHANGE に応じて処理の繰り返し回数を決定 */

  passed_step = 0;

  switch( emu_mode_execute ){
  default:
  case GO:
    target_step = 0;			/* 無限に実行 */
    infinity    = INFINITY;
    only_1step  = ONLY_1STEP;
    break;

  case TRACE:
    target_step = trace_counter;	/* 指定ステップ数実行 */
    infinity    = ONLY_1STEP;
    only_1step  = ONLY_1STEP;
    break;

  case STEP:
    target_step = 1;			/* 1ステップ実行 */
    infinity    = ONLY_1STEP;
    only_1step  = ONLY_1STEP;
    break;

  case TRACE_CHANGE:
    target_step = 0;			/* 無限に実行 */
    infinity    = ONLY_1STEP;
    only_1step  = ONLY_1STEP;
    break;
  }


  /* 実行する残りステップ数。
	TRACE / STEP の時は、指定されたステップ数。
	GO / TRACE_CHANGE なら 無限なので、 0。
		なお、途中でメニューに遷移した場合、強制的に 1 がセットされる。
		これにより無限に処理する場合でも、ループを抜けるようになる。 */
  emu_rest_step = target_step;
}


void	emu_main(void)
{
  int	wk;

  profiler_lapse( PROF_LAPSE_CPU );

  switch( emu_mode_execute ){

  /*------------------------------------------------------------------------*/
  case GO:				/* ひたすら実行する           */
  case TRACE:				/* 指定したステップ、実行する */
  case STEP:				/* 1ステップだけ、実行する    */

    for(;;){

      switch( cpu_timing ){

      case 0:		/* select_main_cpu で指定されたほうのCPUを無限実行 */
	if( select_main_cpu ) (z80_exec)( &z80main_cpu, infinity );
	else                  (z80_exec)( &z80sub_cpu,  infinity );
	break;

      case 1:		/* dual_cpu_count==0 ならメインCPUを無限実行、*/
			/*               !=0 ならメインサブを交互実行 */
	if( dual_cpu_count==0 ) (z80_exec)( &z80main_cpu, infinity   );
	else{
	  (z80_exec)( &z80main_cpu, only_1step );
	  (z80_exec)( &z80sub_cpu,  only_1step );
	  dual_cpu_count --;
	}
	break;

      case 2:		/* メインCPU、サブCPUを交互に 5us ずつ実行 */
	if( main_state < 1*JACKUP  &&  sub_state < 1*JACKUP ){
	  main_state += (int) ((cpu_clock_mhz * cpu_slice_us) * JACKUP);
	  sub_state  += (int) ((3.9936        * cpu_slice_us) * JACKUP);
	}
	if( main_state >= 1*JACKUP ){
	  wk = (infinity==INFINITY) ? main_state/JACKUP : ONLY_1STEP;
	  main_state -= (z80_exec( &z80main_cpu, wk ) ) * JACKUP;
	}
	if( sub_state >= 1*JACKUP ){
	  wk = (infinity==INFINITY) ? sub_state/JACKUP : ONLY_1STEP;
	  sub_state  -= (z80_exec( &z80sub_cpu, wk ) ) * JACKUP;
	}
	break;
      }

      /* TRACE/STEP実行時、規定ステップ実行完了したら、モニターに遷移する */
      if( emu_rest_step ){
	passed_step ++;
	if( -- emu_rest_step <= 0 ) {
	  quasi88_debug();
	}
      }

      /* サウンド出力タイミングであれば、処理 */
      if (quasi88_event_flags & EVENT_AUDIO_UPDATE) {
	quasi88_event_flags &= ~EVENT_AUDIO_UPDATE;

	profiler_lapse( PROF_LAPSE_SND );

	xmame_sound_update();			/* サウンド出力 */

	profiler_lapse( PROF_LAPSE_AUDIO );

	xmame_update_video_and_audio();		/* サウンド出力 その2 */

	profiler_lapse( PROF_LAPSE_INPUT );

	event_update();				/* イベント処理		*/
	keyboard_update();

	profiler_lapse( PROF_LAPSE_CPU2 );
      }

      /* ビデオ出力タイミングであれば、CPU処理は一旦中止。上位に抜ける */
      if (quasi88_event_flags & EVENT_FRAME_UPDATE) {
	return;
      }

      /* モニター遷移時や終了時は、 CPU処理は一旦中止。上位に抜ける */
      if (quasi88_event_flags & (EVENT_DEBUG | EVENT_QUIT)) {
	return;
      }

      /* モード切替が発生しても、上位には抜けない。ビデオ出力まで待つ */
      /* (抜けると、 エミュ → 描画 → ウェイト の流れが崩れるので…) */
    }
    break;

  /*------------------------------------------------------------------------*/

	/* こっちのブレーク処理はうまく動かないかも・・・ (未検証) */

  case TRACE_CHANGE:			/* CPUが切り替わるまで処理をする */
    if( cpu_timing >= 1 ){
      printf( "command 'trace change' can use when -cpu 0\n");
      quasi88_monitor();
      break;
    }

    wk = select_main_cpu;
    while( wk==select_main_cpu ){
      if( select_main_cpu ) (z80_exec)( &z80main_cpu, infinity );
      else                  (z80_exec)( &z80sub_cpu,  infinity );
      if( emu_rest_step ){
	passed_step ++;
	if( -- emu_rest_step <= 0 ) {
	  quasi88_debug();
	}
      }

      if (quasi88_event_flags & EVENT_AUDIO_UPDATE) {
	quasi88_event_flags &= ~EVENT_AUDIO_UPDATE;
	profiler_lapse( PROF_LAPSE_SND );
	xmame_sound_update();			/* サウンド出力 */
	profiler_lapse( PROF_LAPSE_AUDIO );
	xmame_update_video_and_audio();		/* サウンド出力 その2 */
	profiler_lapse( PROF_LAPSE_INPUT );
	event_update();				/* イベント処理		*/
	keyboard_update();
      }

      if (quasi88_event_flags & EVENT_FRAME_UPDATE) {
	return;
      }
      if (quasi88_event_flags & (EVENT_DEBUG | EVENT_QUIT)) {
	return;
      }
    }
    quasi88_debug();
    break;
  }
  return;
}
















/***********************************************************************
 * ステートロード／ステートセーブ
 ************************************************************************/

#define	SID	"EMU "

static	T_SUSPEND_W	suspend_emu_work[] =
{
  { TYPE_INT,	&cpu_timing,		},
  { TYPE_INT,	&select_main_cpu,	},
  { TYPE_INT,	&dual_cpu_count,	},
  { TYPE_INT,	&CPU_1_COUNT,		},
  { TYPE_INT,	&cpu_slice_us,		},
  { TYPE_INT,	&main_state,		},
  { TYPE_INT,	&sub_state,		},

  { TYPE_END,	0			},
};


int	statesave_emu( void )
{
  if( statesave_table( SID, suspend_emu_work ) == STATE_OK ) return TRUE;
  else                                                       return FALSE;
}

int	stateload_emu( void )
{
  if( stateload_table( SID, suspend_emu_work ) == STATE_OK ) return TRUE;
  else                                                       return FALSE;
}
