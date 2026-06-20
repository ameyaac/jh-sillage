import customtkinter as ctk
from ui.widgets import TagEntry, LabeledField, StarRating
from model.predictor import predict, get_verdict
from data.csv_store import save_entry, load_all

MEDIUMS = [
    "Spray Perfume (EDP)",
    "Spray Perfume (EDT)",
    "Spray Perfume (EDC)",
    "Roll-on",
    "Body Mist",
    "Perfume Oil",
    "Wax Perfume (Solid)",
    "Attar",
]
STRENGTHS = ["Sillage (Intense)", "Heavy", "Medium", "Light", "Subtle / Skin Scent"]
FAMILIES = [
    "",
    "Floral",
    "Woody",
    "Oriental",
    "Fresh",
    "Citrus",
    "Aquatic",
    "Gourmand",
    "Spicy",
    "Fougere",
    "Chypre",
    "Aromatic",
    "Musk",
]


class FormTab(ctk.CTkFrame):
    def __init__(self, master, on_save=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_save = on_save
        self._build()

    def _build(self):
        left = ctk.CTkScrollableFrame(self, width=440)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right = ctk.CTkFrame(self, width=280)
        right.pack(side="right", fill="y", padx=(8, 0))
        right.pack_propagate(False)
        self._build_form(left)
        self._build_prediction(right)

    def _build_form(self, parent):
        pad = {"padx": 12, "pady": 5}

        ctk.CTkLabel(
            parent, text="Perfume Details", font=ctk.CTkFont(size=15, weight="bold")
        ).pack(anchor="w", **pad)

        for attr, label, ph in [
            ("_brand", "Brand", "e.g. Chanel, Dior, Byredo"),
            ("_name", "Scent Name", "e.g. Bleu de Chanel"),
        ]:
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", **pad)
            ctk.CTkLabel(
                row, text=label, font=ctk.CTkFont(size=12, weight="bold")
            ).pack(anchor="w")
            entry = ctk.CTkEntry(row, placeholder_text=ph)
            entry.pack(fill="x")
            setattr(self, attr, entry)

        # Medium
        row3 = ctk.CTkFrame(parent, fg_color="transparent")
        row3.pack(fill="x", **pad)
        ctk.CTkLabel(
            row3, text="Scent Medium  *", font=ctk.CTkFont(size=12, weight="bold")
        ).pack(anchor="w")
        ctk.CTkLabel(
            row3,
            text="How is the perfume delivered?",
            font=ctk.CTkFont(size=10),
            text_color=("gray55", "gray60"),
        ).pack(anchor="w")
        self._medium = ctk.CTkOptionMenu(
            row3, values=MEDIUMS, command=lambda _: self._live_predict()
        )
        self._medium.set("Spray Perfume (EDP)")
        self._medium.pack(fill="x")

        # Strength
        row4 = ctk.CTkFrame(parent, fg_color="transparent")
        row4.pack(fill="x", **pad)
        ctk.CTkLabel(
            row4, text="Scent Strength  *", font=ctk.CTkFont(size=12, weight="bold")
        ).pack(anchor="w")
        ctk.CTkLabel(
            row4,
            text="How strong / projecting is it?",
            font=ctk.CTkFont(size=10),
            text_color=("gray55", "gray60"),
        ).pack(anchor="w")
        self._strength = ctk.CTkOptionMenu(
            row4, values=STRENGTHS, command=lambda _: self._live_predict()
        )
        self._strength.set("Medium")
        self._strength.pack(fill="x")

        # Scent Family (optional)
        row5 = ctk.CTkFrame(parent, fg_color="transparent")
        row5.pack(fill="x", **pad)
        r5h = ctk.CTkFrame(row5, fg_color="transparent")
        r5h.pack(fill="x")
        ctk.CTkLabel(
            r5h, text="Scent Family", font=ctk.CTkFont(size=12, weight="bold")
        ).pack(side="left")
        ctk.CTkLabel(
            r5h,
            text="  optional",
            font=ctk.CTkFont(size=10),
            text_color=("gray55", "gray60"),
        ).pack(side="left")
        self._family = ctk.CTkOptionMenu(
            row5, values=FAMILIES, command=lambda _: self._live_predict()
        )
        self._family.set("")
        self._family.pack(fill="x")

        ctk.CTkLabel(
            parent, text="Fragrance Pyramid", font=ctk.CTkFont(size=15, weight="bold")
        ).pack(anchor="w", padx=12, pady=(14, 4))

        # Notes
        note_specs = [
            ("_top", "Top Notes", "bergamot, lemon, grapefruit..."),
            ("_mid", "Heart / Middle Notes", "rose, jasmine, iris..."),
            ("_base", "Base Notes", "sandalwood, amber, musk..."),
        ]
        hints = ["first impression", "core character", "dry-down, lasting"]
        for (attr, label, ph), hint in zip(note_specs, hints):
            f = ctk.CTkFrame(parent, fg_color="transparent")
            f.pack(fill="x", padx=12, pady=4)
            LabeledField(f, label, hint=hint).pack(fill="x")
            widget = TagEntry(f, placeholder=ph)
            widget.pack(fill="x", pady=4)
            widget._entry.bind("<KeyRelease>", lambda e: self._live_predict())
            setattr(self, attr, widget)

        # Personal notes textarea
        f_notes = ctk.CTkFrame(parent, fg_color="transparent")
        f_notes.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(
            f_notes, text="Your Notes", font=ctk.CTkFont(size=12, weight="bold")
        ).pack(anchor="w")
        ctk.CTkLabel(
            f_notes,
            text="optional",
            font=ctk.CTkFont(size=10),
            text_color=("gray55", "gray60"),
        ).pack(anchor="w")
        self._notes_txt = ctk.CTkTextbox(f_notes, height=60)
        self._notes_txt.pack(fill="x", pady=4)

        # Actual rating
        f_rate = ctk.CTkFrame(parent, fg_color="transparent")
        f_rate.pack(fill="x", padx=12, pady=8)
        ctk.CTkLabel(
            f_rate, text="Actual Rating", font=ctk.CTkFont(size=12, weight="bold")
        ).pack(anchor="w")
        ctk.CTkLabel(
            f_rate,
            text="Rate AFTER smelling. Leave at 0 if not tried yet.",
            font=ctk.CTkFont(size=10),
            text_color=("gray55", "gray60"),
        ).pack(anchor="w")
        self._actual_rating = StarRating(f_rate)
        self._actual_rating.pack(anchor="w", pady=4)

        # Buttons
        btn_frame = ctk.CTkFrame(parent, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=10)
        ctk.CTkButton(
            btn_frame,
            text="Save Entry",
            fg_color="#7c4d8a",
            hover_color="#61367a",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._save,
        ).pack(fill="x", pady=4)
        ctk.CTkButton(
            btn_frame,
            text="Clear Form",
            fg_color="transparent",
            border_width=1,
            text_color=("gray40", "gray70"),
            border_color=("gray70", "gray40"),
            command=self._clear,
        ).pack(fill="x")

    def _build_prediction(self, parent):
        ctk.CTkLabel(
            parent, text="Live Prediction", font=ctk.CTkFont(size=13, weight="bold")
        ).pack(pady=(16, 4), padx=12)
        self._score_lbl = ctk.CTkLabel(
            parent,
            text="--",
            font=ctk.CTkFont(family="Georgia", size=52),
            text_color=("#7c4d8a", "#b87ec8"),
        )
        self._score_lbl.pack()
        self._stars_lbl = ctk.CTkLabel(
            parent,
            text="",
            font=ctk.CTkFont(size=20),
            text_color=("#c97d3a", "#e0a050"),
        )
        self._stars_lbl.pack()
        self._verdict_lbl = ctk.CTkLabel(
            parent,
            text="Add notes to see prediction",
            font=ctk.CTkFont(size=11, slant="italic"),
            text_color=("gray55", "gray60"),
            wraplength=230,
        )
        self._verdict_lbl.pack(pady=(4, 12), padx=12)

        ctk.CTkLabel(
            parent, text="Breakdown", font=ctk.CTkFont(size=12, weight="bold")
        ).pack(anchor="w", padx=12)
        self._breakdown_frame = ctk.CTkScrollableFrame(parent, height=180)
        self._breakdown_frame.pack(fill="x", padx=8, pady=4)

        info = ctk.CTkFrame(parent, fg_color=("#f3ecf8", "#2d1f38"), corner_radius=10)
        info.pack(fill="x", padx=8, pady=8)
        info_text = (
            "How scoring works\n\n"
            "Notes are matched against your "
            "preference profile. Medium and "
            "strength adjust the score.\n\n"
            "Rate entries after smelling "
            "to teach the model your taste."
        )
        ctk.CTkLabel(
            info,
            text=info_text,
            font=ctk.CTkFont(size=10),
            justify="left",
            text_color=("gray40", "gray75"),
            wraplength=240,
        ).pack(padx=10, pady=10)

    def _live_predict(self, *_):
        result = predict(
            self._top.get_tags(),
            self._mid.get_tags(),
            self._base.get_tags(),
            scent_family=self._family.get(),
            scent_medium=self._medium.get(),
            scent_strength=self._strength.get(),
            log_entries=load_all(),
        )
        if not result:
            self._score_lbl.configure(text="--")
            self._stars_lbl.configure(text="")
            self._verdict_lbl.configure(text="Add notes to see prediction")
            for w in self._breakdown_frame.winfo_children():
                w.destroy()
            return

        s = result["total"]
        self._score_lbl.configure(text=f"{s:.1f}")
        full = int(s)
        half = "1/2" if (s - full) >= 0.5 else ""
        empty = 5 - full - (1 if half else 0)
        self._stars_lbl.configure(text="★" * full + half + "☆" * empty)
        self._verdict_lbl.configure(text=f'"{get_verdict(s)}"')

        for w in self._breakdown_frame.winfo_children():
            w.destroy()
        for label, val in result["breakdown"].items():
            row = ctk.CTkFrame(self._breakdown_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(
                row,
                text=label,
                font=ctk.CTkFont(size=10),
                text_color=("gray50", "gray65"),
                width=90,
                anchor="w",
            ).pack(side="left")
            bar_bg = ctk.CTkFrame(
                row, height=6, corner_radius=3, fg_color=("gray85", "gray30")
            )
            bar_bg.pack(side="left", fill="x", expand=True, padx=4)
            pct = max(0, min(1, val / 5.0))
            fill_bar = ctk.CTkFrame(
                bar_bg,
                height=6,
                corner_radius=3,
                fg_color=("#7c4d8a", "#b87ec8"),
                width=int(160 * pct),
            )
            fill_bar.place(x=0, y=0)
            ctk.CTkLabel(
                row,
                text=f"{val}",
                font=ctk.CTkFont(size=10, weight="bold"),
                width=28,
                anchor="e",
            ).pack(side="right")

    def _save(self):
        tops = self._top.get_tags()
        mids = self._mid.get_tags()
        bases = self._base.get_tags()
        if not tops and not mids and not bases:
            return
        result = predict(
            tops,
            mids,
            bases,
            scent_family=self._family.get(),
            scent_medium=self._medium.get(),
            scent_strength=self._strength.get(),
            log_entries=load_all(),
        )
        actual = self._actual_rating.get()
        entry = {
            "brand_name": self._brand.get().strip() or "--",
            "scent_name": self._name.get().strip() or "--",
            "scent_medium": self._medium.get(),
            "scent_family": self._family.get(),
            "scent_strength": self._strength.get(),
            "top_notes": "; ".join(tops),
            "middle_notes": "; ".join(mids),
            "base_notes": "; ".join(bases),
            "predicted_rating": result["total"] if result else 0,
            "actual_rating": actual if actual > 0 else "",
            "notes": self._notes_txt.get("1.0", "end").strip(),
        }
        save_entry(entry)
        if self._on_save:
            self._on_save()
        self._clear()

    def _clear(self):
        self._brand.delete(0, "end")
        self._name.delete(0, "end")
        self._medium.set("Spray Perfume (EDP)")
        self._strength.set("Medium")
        self._family.set("")
        self._top.clear()
        self._mid.clear()
        self._base.clear()
        self._notes_txt.delete("1.0", "end")
        self._actual_rating.reset()
        self._score_lbl.configure(text="--")
        self._stars_lbl.configure(text="")
        self._verdict_lbl.configure(text="Add notes to see prediction")
        for w in self._breakdown_frame.winfo_children():
            w.destroy()
