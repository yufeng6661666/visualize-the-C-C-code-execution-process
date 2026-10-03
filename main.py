"""
C/C++ 代码逐步执行可视化调试器

左侧显示代码并高亮当前执行行，右侧显示各作用域的变量值，
支持逐行前进 / 回退，并且可以为变量添加备注。
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from typing import Any, Dict, Optional

from c_interpreter import interpret, tokenize, Parser, Executor


SAMPLE_CODE = """#include <stdio.h>

// 计算斐波那契数列前 n 项
int main() {
    int n = 10;
    int a = 0, b = 1;
    int i;
    printf("Fibonacci: ");
    for (i = 0; i < n; i++) {
        printf("%d ", a);
        int next = a + b;
        a = b;
        b = next;
    }
    printf("\\n");
    return 0;
}
"""


class CodeEditor(ttk.Frame):
    """带行号的代码编辑区，支持当前行高亮。"""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.line_numbers = tk.Text(self, width=5, padx=4, takefocus=0,
                                    border=0, background="#f0f0f0",
                                    foreground="#888", state="disabled",
                                    font=("Consolas", 11))
        self.line_numbers.pack(side="left", fill="y")

        self.text = tk.Text(self, wrap="none", font=("Consolas", 11),
                            undo=True, border=1, relief="sunken")
        self.text.pack(side="left", fill="both", expand=True)

        self.scroll = ttk.Scrollbar(self, orient="vertical",
                                    command=self._on_scroll)
        self.scroll.pack(side="right", fill="y")
        self.text.configure(yscrollcommand=self._sync_scroll)
        self.line_numbers.configure(yscrollcommand=self._sync_scroll)

        self.text.tag_configure("highlight", background="#fff3a0",
                                relief="flat")
        self.text.tag_configure("current_line", background="#e0f0ff")
        self.text.bind("<KeyRelease>", self._update_line_numbers)
        self.text.bind("<MouseWheel>", self._on_mousewheel)
        self._update_line_numbers()

    def _on_scroll(self, *args):
        self.text.yview(*args)
        self.line_numbers.yview(*args)

    def _sync_scroll(self, first, last):
        self.scroll.set(first, last)
        self.line_numbers.yview("moveto", first)

    def _on_mousewheel(self, event):
        self.text.yview_scroll(int(-event.delta / 120), "units")
        self.line_numbers.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _update_line_numbers(self, event=None):
        lines = self.text.get("1.0", "end-1c").split("\n")
        line_count = len(lines)
        nums = "\n".join(str(i) for i in range(1, line_count + 1))
        self.line_numbers.configure(state="normal")
        self.line_numbers.delete("1.0", "end")
        self.line_numbers.insert("1.0", nums)
        self.line_numbers.configure(state="disabled")

    def get_code(self) -> str:
        return self.text.get("1.0", "end-1c")

    def set_code(self, code: str):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", code)
        self._update_line_numbers()

    def highlight_line(self, line_no: int):
        """高亮指定行（1-based）。line_no <= 0 表示清除高亮。"""
        self.text.tag_remove("highlight", "1.0", "end")
        if line_no and line_no > 0:
            start = f"{line_no}.0"
            end = f"{line_no}.end"
            self.text.tag_add("highlight", start, end)
            # 滚动到该行
            self.text.see(start)
            self.line_numbers.see(start)

    def clear_highlight(self):
        self.text.tag_remove("highlight", "1.0", "end")


class VariablePanel(ttk.Frame):
    """右侧变量面板：按作用域列出变量，并提供备注输入框。"""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.notes = {}  # (scope_key, name) -> str
        # 数组显示范围表达式（字符串，可为变量/四则运算，如 "n", "n+1", "2*n"）
        self.array_start_expr = "0"
        self.array_count_expr = "0"  # 0 = 自动推断
        self._avail_vars: Dict[str, Any] = {}

        # 标题
        header = ttk.Label(self, text="变量 (Variables)",
                           font=("Microsoft YaHei", 11, "bold"))
        header.pack(anchor="w", padx=8, pady=(6, 2))

        # 可滚动区域
        self.canvas = tk.Canvas(self, border=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical",
                                       command=self.canvas.yview)
        self.scroll_frame = ttk.Frame(self.canvas)

        self.scroll_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas_window = self.canvas.create_window(
            (0, 0), window=self.scroll_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas_window_width = 0
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self.scrollbar.pack(side="right", fill="y")

        # 鼠标滚轮
        def _wheel(event):
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        self.canvas.bind("<MouseWheel>", _wheel)

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.canvas_window, width=event.width)

    def clear(self):
        for child in self.scroll_frame.winfo_children():
            child.destroy()

    def set_variables(self, snapshot: dict):
        """根据快照刷新变量显示。"""
        self.clear()
        # 收集当前可用的数值变量（用于求值数组显示范围表达式）
        avail: Dict[str, Any] = {}
        for k, info in snapshot.get("global", {}).items():
            v = info["value"]
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                avail[k] = v
        for frame in snapshot.get("stack", []):
            for k, info in frame["vars"].items():
                v = info["value"]
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    avail[k] = v  # 内层作用域覆盖外层
        self._avail_vars = avail

        row = 0

        # 全局变量
        g = snapshot.get("global", {})
        if g:
            row = self._add_section(row, "全局 Global", g, "global")

        # 调用栈中的局部变量（从底到顶）
        for frame in snapshot.get("stack", []):
            func = frame["func"]
            vars_ = frame["vars"]
            if vars_:
                row = self._add_section(row, f"函数 {func}()", vars_, func)

        if row == 0:
            ttk.Label(self.scroll_frame, text="（暂无变量）",
                      foreground="#999").grid(row=0, column=0, padx=10,
                                               pady=10, sticky="w")

    def _add_section(self, row, title, vars_dict, scope_key):
        lbl = ttk.Label(self.scroll_frame, text=title,
                        font=("Microsoft YaHei", 10, "bold"),
                        foreground="#2c5282")
        lbl.grid(row=row, column=0, columnspan=4, sticky="w",
                 padx=8, pady=(8, 2))
        row += 1
        for name, info in vars_dict.items():
            vtype = info["type"]
            value = info["value"]
            modified = info.get("modified", [])
            value_str = self._format_value(value, modified)

            name_lbl = ttk.Label(self.scroll_frame, text=name,
                                 font=("Consolas", 10, "bold"),
                                 foreground="#2d3748")
            name_lbl.grid(row=row, column=0, sticky="w", padx=(14, 4))

            type_lbl = ttk.Label(self.scroll_frame, text=vtype,
                                 font=("Consolas", 9),
                                 foreground="#718096")
            type_lbl.grid(row=row, column=1, sticky="w", padx=4)

            eq_lbl = ttk.Label(self.scroll_frame, text="=",
                               font=("Consolas", 10))
            eq_lbl.grid(row=row, column=2, sticky="w", padx=2)

            val_lbl = ttk.Label(self.scroll_frame, text=value_str,
                                font=("Consolas", 10),
                                foreground="#2f855a",
                                wraplength=380, justify="left")
            val_lbl.grid(row=row, column=3, sticky="w", padx=4)

            # 备注输入框
            note_key = (scope_key, name)
            note_var = tk.StringVar(value=self.notes.get(note_key, ""))
            note_entry = ttk.Entry(self.scroll_frame, textvariable=note_var,
                                   width=22, font=("Microsoft YaHei", 9))
            note_entry.grid(row=row, column=4, sticky="we", padx=6, pady=1)

            def make_save(key, var):
                def save(_event=None):
                    self.notes[key] = var.get()
                return save

            note_entry.bind("<KeyRelease>", make_save(note_key, note_var))
            note_entry.bind("<FocusOut>", make_save(note_key, note_var))
            row += 1

        # 分隔线
        sep = ttk.Separator(self.scroll_frame, orient="horizontal")
        sep.grid(row=row, column=0, columnspan=5, sticky="ew",
                 padx=8, pady=4)
        row += 1
        return row

    def _eval_range_expr(self, expr: str) -> Optional[int]:
        """求值数组显示范围表达式（支持变量和四则运算）。失败返回 None。"""
        expr = (expr or "").strip()
        if expr == "":
            return None
        try:
            val = eval(expr, {"__builtins__": {}}, dict(self._avail_vars))
            if isinstance(val, bool):
                return int(val)
            if isinstance(val, (int, float)):
                return int(val)
            return None
        except Exception:
            return None

    def _format_array_auto(self, value, modified) -> str:
        """默认模式：仅显示被修改过的元素，其余用省略号表示。"""
        total = len(value)
        sorted_mod = sorted(i for i in modified if 0 <= i < total)
        if not sorted_mod:
            return f"[ ... (共 {total} 项) ]"
        parts = []
        prev = -1
        for idx in sorted_mod:
            gap = idx - prev - 1
            if gap > 0:
                if prev == -1:
                    parts.append(f"... (前 {gap} 项省略)")
                else:
                    parts.append(f"... ({gap} 项省略)")
            parts.append(self._format_value(value[idx]))
            prev = idx
        tail = total - 1 - prev
        if tail > 0:
            parts.append(f"... (后 {tail} 项省略)")
        return "[" + ", ".join(parts) + "]"

    def _format_value(self, value, modified=None) -> str:
        if isinstance(value, list):
            total = len(value)
            modified = modified or []
            # 用户是否明确指定了显示项数 Y（Y>0 视为显式指定）
            count = self._eval_range_expr(self.array_count_expr)
            explicit = count is not None and count > 0

            if not explicit:
                # 默认模式：仅显示被修改过的元素
                return self._format_array_auto(value, modified)

            # 显式指定范围模式：从第 X 项起显示 Y 项
            start = self._eval_range_expr(self.array_start_expr)
            if start is None:
                start = 0
            if start < 0:
                start = 0
            if start >= total:
                return f"[ (起始位置 {start} 超出范围，数组共 {total} 项) ]"
            if count < 0:
                count = 0
            end = start + count
            if end > total:
                end = total
            shown = value[start:end]
            inner = ", ".join(self._format_value(v) for v in shown)
            parts = []
            if start > 0:
                parts.append(f"... (前 {start} 项省略)")
            parts.append(inner)
            if end < total:
                parts.append(f"... (后 {total - end} 项省略)")
            suffix = f"  [显示项 {start}~{end - 1} / 共 {total} 项]"
            body = " ".join(p for p in parts if p)
            return f"[{body}]{suffix}"
        if isinstance(value, str):
            return repr(value)
        if isinstance(value, bool):
            return str(int(value))
        if isinstance(value, float):
            return f"{value:.6g}"
        return str(value)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("C/C++ 逐步执行可视化调试器")
        self.geometry("1180x760")
        self.minsize(900, 600)

        self.executor: Executor = None
        self.current_step = 0

        self._build_ui()
        self.code_editor.set_code(SAMPLE_CODE)

    def _build_ui(self):
        # 顶部工具栏
        toolbar = ttk.Frame(self, padding=(8, 6))
        toolbar.pack(fill="x")

        self.run_btn = ttk.Button(toolbar, text="▶ 编译运行",
                                  command=self.run_code)
        self.run_btn.pack(side="left", padx=3)

        self.back_btn = ttk.Button(toolbar, text="◀ 上一步",
                                   command=self.step_back, state="disabled")
        self.back_btn.pack(side="left", padx=3)

        self.next_btn = ttk.Button(toolbar, text="下一步 ▶",
                                   command=self.step_forward, state="disabled")
        self.next_btn.pack(side="left", padx=3)

        self.reset_btn = ttk.Button(toolbar, text="⟲ 重置",
                                    command=self.reset_run, state="disabled")
        self.reset_btn.pack(side="left", padx=3)

        self.step_label = ttk.Label(toolbar, text="步数: 0 / 0",
                                    font=("Microsoft YaHei", 10))
        self.step_label.pack(side="left", padx=12)

        ttk.Label(toolbar, text="scanf 输入 (空格分隔):").pack(side="left",
                                                                 padx=(20, 4))
        self.input_entry = ttk.Entry(toolbar, width=16)
        self.input_entry.pack(side="left")

        # 数组显示范围：从第 X 项起，显示 Y 项 (X,Y 可为变量表达式)
        ttk.Label(toolbar, text="数组: 从第").pack(side="left", padx=(16, 2))
        self.array_x_var = tk.StringVar(value="0")
        self.array_x_entry = ttk.Entry(toolbar, width=8,
                                       textvariable=self.array_x_var)
        self.array_x_entry.pack(side="left")
        ttk.Label(toolbar, text="项起，显示").pack(side="left", padx=2)
        self.array_y_var = tk.StringVar(value="0")
        self.array_y_entry = ttk.Entry(toolbar, width=8,
                                       textvariable=self.array_y_var)
        self.array_y_entry.pack(side="left")
        ttk.Label(toolbar, text="项 (0=自动, 可用变量如 n, n+1)").pack(side="left",
                                                                       padx=2)
        for w in (self.array_x_entry, self.array_y_entry):
            w.bind("<KeyRelease>", self._on_array_range_change)
            w.bind("<FocusOut>", self._on_array_range_change)

        # 主分割区
        self.paned = ttk.PanedWindow(self, orient="horizontal")
        self.paned.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        # 左侧代码区
        self.code_editor = CodeEditor(self.paned)
        self.paned.add(self.code_editor, weight=3)

        # 右侧变量区
        self.var_panel = VariablePanel(self.paned)
        self.paned.add(self.var_panel, weight=2)

        # 底部输出区
        bottom = ttk.LabelFrame(self, text="输出 Output", padding=4)
        bottom.pack(fill="x", padx=8, pady=(0, 8))
        self.output_text = scrolledtext.ScrolledText(
            bottom, height=8, font=("Consolas", 10), state="disabled",
            background="#1a202c", foreground="#e2e8f0", insertbackground="white")
        self.output_text.pack(fill="x")

        # 状态栏
        self.status = ttk.Label(self, text="就绪", anchor="w",
                                relief="sunken", padding=(6, 2))
        self.status.pack(fill="x", side="bottom")

    # ---- 执行控制 ----
    def run_code(self):
        code = self.code_editor.get_code()
        if not code.strip():
            messagebox.showwarning("提示", "请先输入 C/C++ 代码")
            return
        input_text = self.input_entry.get().strip()
        input_lines = input_text.split() if input_text else []
        try:
            self.executor = interpret(code, input_lines=input_lines)
        except SyntaxError as e:
            self._set_output(f"[语法错误] {e}\n")
            self.status.config(text=f"语法错误: {e}")
            return
        except Exception as e:
            self._set_output(f"[错误] {e}\n")
            self.status.config(text=f"错误: {e}")
            return

        self.current_step = 0
        self.next_btn.config(state="normal")
        self.back_btn.config(state="disabled")
        self.reset_btn.config(state="normal")
        self.code_editor.clear_highlight()
        self._refresh()
        if self.executor._error:
            self.status.config(text=f"运行出错: {self.executor._error}")
        else:
            self.status.config(text=f"编译运行完成，共 {len(self.executor.history) - 1} 步")

    def step_forward(self):
        if self.executor is None:
            return
        if self.current_step < len(self.executor.history) - 1:
            self.current_step += 1
            self._refresh()

    def step_back(self):
        if self.executor is None:
            return
        if self.current_step > 0:
            self.current_step -= 1
            self._refresh()

    def reset_run(self):
        if self.executor is None:
            return
        self.current_step = 0
        self.code_editor.clear_highlight()
        self._refresh()

    def _apply_array_range(self):
        self.var_panel.array_start_expr = self.array_x_var.get()
        self.var_panel.array_count_expr = self.array_y_var.get()

    def _on_array_range_change(self, _event=None):
        self._apply_array_range()
        if self.executor is not None:
            self._refresh()

    def _refresh(self):
        if self.executor is None:
            return
        # apply array display range setting
        self._apply_array_range()
        snap = self.executor.history[self.current_step]
        # 高亮
        line = snap["line"]
        if line and line > 0:
            self.code_editor.highlight_line(line)
        else:
            self.code_editor.clear_highlight()
        # 变量
        self.var_panel.set_variables(snap)
        # 输出
        self._set_output(snap["output"])
        # 步数
        total = len(self.executor.history) - 1
        self.step_label.config(text=f"步数: {self.current_step} / {total}")
        # 按钮状态
        self.back_btn.config(state="normal" if self.current_step > 0 else "disabled")
        self.next_btn.config(state="normal" if self.current_step < total else "disabled")
        # 状态
        if snap.get("aborted"):
            self.status.config(text=f"已中止: {snap.get('error', '')}")
        elif snap.get("error"):
            self.status.config(text=f"运行错误: {snap['error']}")
        elif self.current_step == 0:
            self.status.config(text="准备执行，请点击「下一步」")
        elif self.current_step == total:
            self.status.config(text="执行结束")
        else:
            self.status.config(text=f"正在执行第 {self.current_step} 步，行 {line}")

    def _set_output(self, text: str):
        self.output_text.configure(state="normal")
        self.output_text.delete("1.0", "end")
        self.output_text.insert("1.0", text)
        self.output_text.configure(state="disabled")


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
