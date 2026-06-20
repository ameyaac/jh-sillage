import customtkinter as ctk

class TagEntry(ctk.CTkFrame):
    """Tag/Chip Input - press 'Enter' or ',' (comma) to add a tag"""

    def __init__(self, master, placeholder="Type and press Enter...", placeholder_text=None, tag_color=None, **kwargs):
        if placeholder_text is not None:
            placeholder = placeholder_text
        super().__init__(master, fg_color="transparent", **kwargs)
        self._tags = []

        self._wrap = ctk.CTkScrollableFrame(self, height=80, fg_color=("grey95", "grey20"))
        self._wrap.pack(fill="x", expand=True)
        self._entry = ctk.CTkEntry(self._wrap, placeholder_text=placeholder, border_width=0, fg_color="transparent", height=28)
        self._entry.pack(side="left", fill="x", expand=True, padx=4, pady=4)
        self._entry.bind("<Return>", self._on_enter)
        self._entry.bind("<KeyRelease>", self._check_comma)
        self._entry.bind("<BackSpace>", self._on_backspace)

    def _on_enter(self, event=None):
        val = self._entry.get().strip().rstrip(",").strip()
        if val:
            self.add_tag(val)
        return "break"

    def _check_comma(self, event=None):
        val = self._entry.get()
        if "," in val:
            self.add_tag(val.replace(",", "").strip())

    def _on_backspace(self, event=None):
        if not self._entry.get() and self._tags:
            self.remove_tag(self._tags[-1])

    def add_tag(self, val):
        val = val.strip().title()
        if not val or val in self._tags:
            return
        self._tags.append(val)
        frame = ctk.CTkFrame(
            self._wrap, fg_color=("#ede0f3", "#3d2e45"), corner_radius=12
        )
        frame.pack(side="left", padx=3, pady=4)
        ctk.CTkLabel(
            frame,
            text=val,
            font=ctk.CTkFont(size=11),
            text_color=("#7c4d8a", "#c8a0e0"),
            padx=6,
        ).pack(side="left")
        ctk.CTkButton(
            frame,
            text="x",
            width=18,
            height=18,
            corner_radius=9,
            fg_color="transparent",
            hover_color=("#d8c0e8", "#5a3d70"),
            text_color=("#7c4d8a", "#c8a0e0"),
            font=ctk.CTkFont(size=11),
            command=lambda v=val, f=frame: self._remove(v, f),
        ).pack(side="left", padx=(0, 3))
        self._entry.delete(0, "end")

    def _remove(self, val, frame):
        if val in self._tags:
            self._tags.remove(val)
        frame.destroy()

    def remove_tag(self, val):
        for widget in self._wrap.winfo_children():
            if isinstance(widget, ctk.CTkFrame):
                for child in widget.winfo_children():
                    if isinstance(child, ctk.CTkLabel) and child.cget("text") == val:
                        if val in self._tags:
                            self._tags.remove(val)
                        widget.destroy()
                        return

    def get_tags(self):
        return list(self._tags)

    def clear(self):
        for widget in self._wrap.winfo_children():
            if isinstance(widget, ctk.CTkFrame):
                widget.destroy()
        self._tags.clear()
        self._entry.delete(0, "end")

class LabeledField(ctk.CTkFrame):
    def __init__(self, master, label, hint="", **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkLabel(row, text=label, font=ctk.CTkFont(size=13, weight="bold")).pack(
            side="left"
        )
        if hint:
            ctk.CTkLabel(
                row,
                text=hint,
                font=ctk.CTkFont(size=11),
                text_color=("gray55", "gray60"),
            ).pack(side="left", padx=6)


class StarRating(ctk.CTkFrame):
    """1-5 star rating selector."""

    def __init__(self, master, command=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._value = 0
        self._command = command
        self._btns = []
        for i in range(1, 6):
            btn = ctk.CTkButton(
                self,
                text="☆",
                width=32,
                height=32,
                font=ctk.CTkFont(size=20),
                fg_color="transparent",
                hover_color=("gray90", "gray25"),
                text_color=("gray50", "gray60"),
                command=lambda v=i: self._set(v),
            )
            btn.pack(side="left", padx=1)
            self._btns.append(btn)

    def _set(self, val):
        self._value = val
        for i, btn in enumerate(self._btns):
            if i < val:
                btn.configure(text="★", text_color=("#c97d3a", "#e0a050"))
            else:
                btn.configure(text="☆", text_color=("gray50", "gray60"))
        if self._command:
            self._command(val)

    def get(self):
        return self._value

    def reset(self):
        self._value = 0
        for btn in self._btns:
            btn.configure(text="☆", text_color=("gray50", "gray60"))
