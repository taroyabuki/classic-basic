#ifndef EMU_H_INCLUDED
#define EMU_H_INCLUDED


extern int cpu_timing;							/* SUB-CPU 駆動方式 */
extern int select_main_cpu;						/* -cpu 0 実行するCPU */
extern int dual_cpu_count;						/* -cpu 1 同時処理STEP数 */
extern int CPU_1_COUNT;							/* その、初期値 */
extern int cpu_slice_us;						/* -cpu 2 処理時分割(us)*/

extern int trace_counter;						/* TRACE 時のカウンタ */

typedef enum {
	QUASI88_EXEC_UNKNOWN = 0,
	QUASI88_EXEC_RUNNING,
	QUASI88_EXEC_READY,
	QUASI88_EXEC_INPUT_WAIT,
	QUASI88_EXEC_COMPLETED,
	QUASI88_EXEC_ERROR
} quasi88_exec_state_t;


typedef struct {								/* ブレークポイント制御 */
	short type;
	word  addr;
} break_t;

typedef struct {								/* FDC ブレークポイント制御 */
	short type;
	short drive;
	short track;
	short sector;
} break_drive_t;

enum BPcpu { BP_MAIN, BP_SUB, EndofBPcpu  };
enum BPtype {
	BP_NONE, BP_PC,  BP_READ, BP_WRITE, BP_IN, BP_OUT,  BP_DIAG,
	EndofBPtype
};

#define NR_BP					(10)			/* ブレークポイントの数 */
#define BP_NUM_FOR_SYSTEM		(9)				/* システムが使うBPの番号 */
extern break_t break_point[2][NR_BP];
extern break_drive_t break_point_fdc[NR_BP];









/**** 関数 ****/

void emu_breakpoint_init(void);
void emu_reset(void);

enum EmuExecMode {
	GO,
	TRACE,
	STEP,
	TRACE_CHANGE
};
void set_emu_exec_mode(int mode);
void clear_exec_state(void);

void emu_init(void);
void emu_main(void);
void emu_status(void);
void emu_status_message_set(int timeout,
							const char *message_ascii,
							const char *message_japanese);
void emu_status_message_clear(void);



int  quasi88_now_subcpu_mode(void);
void quasi88_set_subcpu_mode(int mode);
quasi88_exec_state_t quasi88_get_exec_state(void);
void set_exec_state_by_pc(word pc);
void quasi88_set_rom_hook_addresses(word ready_low, word ready_high,
									word input_wait_pc, word error_pc);
int quasi88_get_was_running_program(void);
void quasi88_set_was_running_program(int value);


#endif /* EMU_H_INCLUDED */
