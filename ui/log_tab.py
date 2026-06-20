import customtkinter as ctk
from data.csv_store import load_all

COLS = [
    "#",
    "Brand",
    "Scent",
    "Medium",
    "Top Notes",
    "Heart Notes",
    "Base Notes",
    "Strength",
    "Predicted",
    "Actual",
    "Date",
]
WIDTHS = [30, 100, 130, 120, 160, 160, 140, 110, 65, 55, 100]


class LogTab(ctk.CTkFrame):
    def __init__(self, master, on_update=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._build()

    def _build(self):
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", padx=8, pady=(8, 4))
        self._count_lbl = ctk.CTkLabel(
            toolbar,
            text="0 entries",
            font=ctk.CTkFont(size=12),
            text_color=("gray55", "gray60"),
        )
        self._count_lbl.pack(side="left")

        self._table_frame = ctk.CTkScrollableFrame(self, orientation="horizontal")
        self._table_frame.pack(fill="both", expand=True, padx=8, pady=4)

        header = ctk.CTkFrame(self._table_frame, fg_color=("#ede0f3", "#3d2e45"))
        header.pack(fill="x")
        for col, w in zip(COLS, WIDTHS):
            ctk.CTkLabel(
                header,
                text=col,
                width=w,
                anchor="w",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=("#7c4d8a", "#c8a0e0"),
            ).pack(side="left", padx=4, pady=6)

        self._rows_frame = ctk.CTkScrollableFrame(self._table_frame)
        self._rows_frame.pack(fill="both", expand=True)

    def refresh(self):
        for w in self._rows_frame.winfo_children():
            w.destroy()

        entries = load_all()
        self._count_lbl.configure(
            text=f"{len(entries)} entr{'y' if len(entries)==1 else 'ies'}"
        )

        if not entries:
            ctk.CTkLabel(
                self._rows_frame,
                text="No entries yet - log your first perfume above.",
                font=ctk.CTkFont(size=12, slant="italic"),
                text_color=("gray60", "gray55"),
            ).pack(pady=40)
            return

        for i, e in enumerate(reversed(entries)):
            bg = ("gray97", "gray18") if i % 2 == 0 else ("gray93", "gray16")
            row = ctk.CTkFrame(self._rows_frame, fg_color=bg, corner_radius=0)
            row.pack(fill="x")
            vals = [
                e.get("id", ""),
                e.get("brand_name", "")[:14],
                e.get("scent_name", "")[:18],
                e.get("scent_medium", "")[:16],
                e.get("top_notes", "")[:22],
                e.get("middle_notes", "")[:22],
                e.get("base_notes", "")[:20],
                e.get("scent_strength", "")[:14],
                e.get("predicted_rating", ""),
                e.get("actual_rating", "") or "--",
                e.get("created_at", "")[:10],
            ]
            for val, w in zip(vals, WIDTHS):
                ctk.CTkLabel(
                    row, text=str(val), width=w, anchor="w", font=ctk.CTkFont(size=11)
                ).pack(side="left", padx=4, pady=5)
