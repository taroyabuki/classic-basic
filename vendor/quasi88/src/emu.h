#ifndef EMU_H_INCLUDED
#define EMU_H_INCLUDED

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

extern	int	cpu_timing;			/* SUB-CPU 駆動 ??	*/
extern	int	select_main_cpu;		/* -cpu 0 実行する CPU	*/
extern	int	dual_cpu_count;			/* -cpu 1 同時処理 STEP 数*/
extern	int	CPU_1_COUNT;			/* その、初期値		*/
extern	int	cpu_slice_us;			/* -cpu 2 処理時分割 (us)*/

extern	int	trace_counter;			/* TRACE 時のカウンタ	*/

/*
 * Execution state API for headless automation
 *
 * EMULATOR EXECUTION STATES:
 *   QUASI88_EXEC_UNKNOWN   - Initial state, or not in EXEC mode, or indeterminate
 *   QUASI88_EXEC_RUNNING   - Emulator is actively executing BASIC program
 *
 * BASIC INTERPRETER STATES (synthetic test verified):
 *   QUASI88_EXEC_READY     - BASIC interpreter at command prompt (READY/"Ok")
 *   QUASI88_EXEC_INPUT_WAIT - BASIC INPUT statement waiting for user input
 *   QUASI88_EXEC_COMPLETED - BASIC program completed normally (END/SYSTEM)
 *   QUASI88_EXEC_ERROR     - BASIC runtime error occurred
 *
 * STATE TRANSITION RULES:
 *   - Entering EXEC mode (quasi88_exec) sets RUNNING
 *   - READY/INPUT_WAIT/COMPLETED/ERROR are set by PC address observation
 *   - Leaving EXEC mode (MONITOR/MENU/PAUSE/QUIT/RESET) clears to UNKNOWN
 *
 * OBSERVATION METHOD:
 *   - PC address monitoring in main_fetch() (pc88main.c)
 *   - No OCR or framebuffer scraping (explicitly forbidden)
 *
 * VERIFICATION:
 *   - Synthetic direct assertion tests: vendor/quasi88/src/test_queue_state.c
 *   - ROM real-execution measurement: TODO (Phase 2, artifacts/rom-state/)
 *   - Runtime state tests: TODO (Phase 3, vendor/quasi88/src/test_runtime_exec_state.c)
 *
 * SEE: emu.c for detailed state semantics
 *      pc88main.c for PC address hook points
 */
typedef enum {
    QUASI88_EXEC_UNKNOWN = 0,
    QUASI88_EXEC_RUNNING,
    QUASI88_EXEC_READY,
    QUASI88_EXEC_INPUT_WAIT,
    QUASI88_EXEC_COMPLETED,
    QUASI88_EXEC_ERROR
} quasi88_exec_state_t;


typedef struct{					/* ブレークポイント制御 */
  short	type;
  word	addr;
} break_t;

typedef struct{					/* FDC ブレークポイント制御 */
  short type;
  short drive;
  short track;
  short sector;
} break_drive_t;

enum BPcpu { BP_MAIN, BP_SUB,                                    EndofBPcpu  };
enum BPtype{ BP_NONE, BP_PC,  BP_READ, BP_WRITE, BP_IN, BP_OUT,  BP_DIAG,
								 EndofBPtype };

#define	NR_BP			(10)		/* ブレークポイントの数   */
#define	BP_NUM_FOR_SYSTEM	(9)		/* システムが使う BP の番号 */
extern	break_t	break_point[2][NR_BP];
extern  break_drive_t break_point_fdc[NR_BP];









	/**** 関数 ****/

void	emu_breakpoint_init( void );
void	emu_reset( void );
void	set_emu_exec_mode( int mode );

/* Internal: clear exec state when leaving EXEC context */
void	clear_exec_state(void);

void	emu_init(void);
void	emu_main(void);

/*
 * Execution state observation API
 *
 * Returns current emulator execution state.
 * See emu.c for detailed state semantics and limitations.
 */
quasi88_exec_state_t quasi88_get_exec_state(void);

/*
 * Internal: Set BASIC interpreter state based on PC observation
 * Called from main_fetch() in pc88main.c when ROM hook addresses are reached.
 */
void set_exec_state_by_pc(word pc);

/*
 * Internal: Configure ROM hook addresses for state observation
 * This must be called before EXEC mode to establish observation points.
 */
void quasi88_set_rom_hook_addresses(word ready_low, word ready_high,
                                     word input_wait_pc, word error_pc);


/*
 * Configuration internal state for testing (DO NOT USE in production)
 */
int quasi88_get_was_running_program(void);
void quasi88_set_was_running_program(int value);

#endif	/* EMU_H_INCLUDED */
