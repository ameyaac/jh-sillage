import customtkinter as ctk
from ui.widgets import TagEntry
from model.predictor import load_profile, save_profile

FAMILIES = [
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
STRENGTHS = ["Subtle / Skin Scent", "Light", "Medium", "Heavy", "Sillage (Intense)"]
OCCASIONS = [
    "Daily wear",
    "Office",
    "Date night",
    "Gym",
    "Evening out",
    "Weekend casual",
    "Special occasions",
    "All seasons",
]
COMMON_NOTES = {
    "Citrus / Fresh": [
        "Bergamot",
        "Lemon",
        "Grapefruit",
        "Neroli",
        "Yuzu",
        "Mandarin",
        "Orange Blossom",
        "Petitgrain",
    ],
    "Floral": [
        "Rose",
        "Jasmine",
        "Iris",
        "Lavender",
        "Violet",
        "Peony",
        "Lily of the Valley",
        "Magnolia",
        "Ylang Ylang",
        "Tuberose",
    ],
    "Woody / Earthy": [
        "Sandalwood",
        "Cedarwood",
        "Vetiver",
        "Patchouli",
        "Oud",
        "Oakwood",
        "Birch",
        "Pine",
    ],
    "Warm / Oriental": [
        "Amber",
        "Vanilla",
        "Tonka Bean",
        "Benzoin",
        "Musk",
        "Incense",
        "Labdanum",
        "Frankincense",
    ],
    "Spice": [
        "Black Pepper",
        "Cardamom",
        "Pink Pepper",
        "Saffron",
        "Cinnamon",
        "Nutmeg",
        "Clove",
    ],
    "Unusual / Animalic": [
        "Civet",
        "Castoreum",
        "Leather",
        "Tobacco",
        "Smoke",
        "Cumin",
        "Hay",
    ],
}


class ProfileTab(ctk.CTkScrollableFrame):
    def __init__(self, master, on_save=None, **kwargs):
        super().__init__(master, **kwargs)
        self._on_save = on_save
        self._family_vars = {}
        self._medium_vars = {}
        self._occasion_vars = {}
        self._note_like_vars = {}
        self._strength_var = ctk.StringVar(value="Medium")
        self._build()
        self._load_existing()

    def _build(self):
        pad = {"padx": 16, "pady": 6}

        # Hero header
        hero = ctk.CTkFrame(self, fg_color=("#f3ecf8", "#2d1f38"), corner_radius=12)
        hero.pack(fill="x", padx=16, pady=(16, 8))
        ctk.CTkLabel(
            hero,
            text="Your Taste Profile",
            font=ctk.CTkFont(family="Georgia", size=18, weight="bold"),
            text_color=("#7c4d8a", "#c8a0e0"),
        ).pack(anchor="w", padx=16, pady=(12, 4))
        hero_desc = (
            "Fill this in once - it becomes the model's starting point. "
            "The more honestly you answer, the better the predictions. "
            "You can update it any time."
        )
        ctk.CTkLabel(
            hero,
            text=hero_desc,
            font=ctk.CTkFont(size=11),
            justify="left",
            text_color=("gray45", "gray65"),
            wraplength=700,
        ).pack(anchor="w", padx=16, pady=(0, 12))

        # Section 1: Families
        self._section("1.  Which scent families do you enjoy?", pad)
        grid1 = ctk.CTkFrame(self, fg_color="transparent")
        grid1.pack(fill="x", **pad)
        for i, fam in enumerate(FAMILIES):
            col = ctk.CTkFrame(grid1, fg_color="transparent")
            col.grid(row=i // 4, column=i % 4, padx=6, pady=4, sticky="w")
            ctk.CTkLabel(
                col, text=fam, font=ctk.CTkFont(size=12), width=90, anchor="w"
            ).pack(anchor="w")
            var = ctk.StringVar(value="neutral")
            self._family_vars[fam] = var
            btn_row = ctk.CTkFrame(col, fg_color="transparent")
            btn_row.pack()
            for val, label in [
                ("love", "Love"),
                ("neutral", "Neutral"),
                ("dislike", "Dislike"),
            ]:
                ctk.CTkRadioButton(
                    btn_row,
                    text=label,
                    value=val,
                    variable=var,
                    font=ctk.CTkFont(size=10),
                    radiobutton_width=14,
                    radiobutton_height=14,
                ).pack(side="left", padx=3)

        # Section 2: Mediums
        self._section("2.  Which formats do you prefer?", pad)
        grid2 = ctk.CTkFrame(self, fg_color="transparent")
        grid2.pack(fill="x", **pad)
        for i, m in enumerate(MEDIUMS):
            var = ctk.BooleanVar()
            self._medium_vars[m] = var
            ctk.CTkCheckBox(
                grid2, text=m, variable=var, font=ctk.CTkFont(size=12)
            ).grid(row=i // 3, column=i % 3, padx=8, pady=4, sticky="w")

        # Section 3: Strength
        self._section("3.  What strength do you prefer?", pad)
        grid3 = ctk.CTkFrame(self, fg_color="transparent")
        grid3.pack(fill="x", **pad)
        for i, s in enumerate(STRENGTHS):
            ctk.CTkRadioButton(
                grid3,
                text=s,
                value=s,
                variable=self._strength_var,
                font=ctk.CTkFont(size=12),
            ).grid(row=0, column=i, padx=10, pady=4, sticky="w")

        # Section 4: Occasions
        self._section("4.  What occasions do you buy perfume for?", pad)
        grid4 = ctk.CTkFrame(self, fg_color="transparent")
        grid4.pack(fill="x", **pad)
        for i, occ in enumerate(OCCASIONS):
            var = ctk.BooleanVar()
            self._occasion_vars[occ] = var
            ctk.CTkCheckBox(
                grid4, text=occ, variable=var, font=ctk.CTkFont(size=12)
            ).grid(row=i // 4, column=i % 4, padx=8, pady=4, sticky="w")

        # Section 5: Note-by-note
        self._section("5.  Rate these notes  (trains the model directly)", pad)
        ctk.CTkLabel(
            self,
            text="Like = enjoy it.  Dislike = puts you off.  -- = unsure/never tried.",
            font=ctk.CTkFont(size=10),
            text_color=("gray55", "gray60"),
        ).pack(anchor="w", padx=16)

        for category, notes in COMMON_NOTES.items():
            cat_frame = ctk.CTkFrame(
                self, fg_color=("gray96", "gray17"), corner_radius=8
            )
            cat_frame.pack(fill="x", padx=16, pady=4)
            ctk.CTkLabel(
                cat_frame,
                text=category,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=("#7c4d8a", "#c8a0e0"),
            ).pack(anchor="w", padx=10, pady=(8, 4))
            note_grid = ctk.CTkFrame(cat_frame, fg_color="transparent")
            note_grid.pack(fill="x", padx=8, pady=(0, 8))
            for j, note in enumerate(notes):
                cell = ctk.CTkFrame(note_grid, fg_color="transparent")
                cell.grid(row=j // 3, column=j % 3, padx=6, pady=3, sticky="w")
                ctk.CTkLabel(
                    cell, text=note, font=ctk.CTkFont(size=11), width=110, anchor="w"
                ).pack(side="left")
                var = ctk.StringVar(value="--")
                self._note_like_vars[note] = var
                ctk.CTkSegmentedButton(
                    cell,
                    values=["Like", "--", "Dislike"],
                    variable=var,
                    font=ctk.CTkFont(size=10),
                    width=130,
                ).pack(side="left", padx=4)

        # Section 6: Free-text
        self._section("6.  Any other notes you love or hate?", pad)
        f_row = ctk.CTkFrame(self, fg_color="transparent")
        f_row.pack(fill="x", **pad)

        love_col = ctk.CTkFrame(f_row, fg_color="transparent")
        love_col.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkLabel(
            love_col,
            text="Notes I love",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("#7c4d8a", "#c8a0e0"),
        ).pack(anchor="w")
        self._extra_loved = TagEntry(
            love_col, placeholder="Type a note and press Enter..."
        )
        self._extra_loved.pack(fill="x", pady=4)

        hate_col = ctk.CTkFrame(f_row, fg_color="transparent")
        hate_col.pack(side="right", fill="x", expand=True, padx=(8, 0))
        ctk.CTkLabel(
            hate_col,
            text="Notes I dislike",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("#a12c3a", "#dd6070"),
        ).pack(anchor="w")
        self._extra_disliked = TagEntry(
            hate_col, placeholder="Type a note and press Enter..."
        )
        self._extra_disliked.pack(fill="x", pady=4)

        # Save button
        save_frame = ctk.CTkFrame(self, fg_color=("gray96", "gray17"), corner_radius=10)
        save_frame.pack(fill="x", padx=16, pady=16)
        ctk.CTkButton(
            save_frame,
            text="Save Taste Profile",
            fg_color="#7c4d8a",
            hover_color="#61367a",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=44,
            command=self._save,
        ).pack(fill="x", padx=12, pady=12)
        self._status_lbl = ctk.CTkLabel(
            save_frame,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=("gray55", "gray60"),
        )
        self._status_lbl.pack(pady=(0, 8))

    def _section(self, title, pad):
        ctk.CTkLabel(self, text=title, font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=16, pady=(16, 2)
        )
        ctk.CTkFrame(self, height=1, fg_color=("gray80", "gray30")).pack(
            fill="x", padx=16, pady=(0, 4)
        )

    def _load_existing(self):
        p = load_profile()
        if not p:
            return
        for fam, var in self._family_vars.items():
            if fam in p.get("loved_families", []):
                var.set("love")
            elif fam in p.get("disliked_families", []):
                var.set("dislike")
        for m, var in self._medium_vars.items():
            var.set(m in p.get("preferred_mediums", []))
        if p.get("preferred_strength"):
            self._strength_var.set(p["preferred_strength"])
        for occ, var in self._occasion_vars.items():
            var.set(occ in p.get("occasions", []))
        note_sent = p.get("note_sentiment", {})
        for note, var in self._note_like_vars.items():
            val = note_sent.get(note, "--")
            var.set(
                "Like" if val == "like" else "Dislike" if val == "dislike" else "--"
            )
        for n in p.get("loved_notes", []):
            self._extra_loved.add_tag(n)
        for n in p.get("disliked_notes", []):
            self._extra_disliked.add_tag(n)

    def _save(self):
        loved_fams = [f for f, v in self._family_vars.items() if v.get() == "love"]
        disliked_fams = [
            f for f, v in self._family_vars.items() if v.get() == "dislike"
        ]
        pref_mediums = [m for m, v in self._medium_vars.items() if v.get()]
        occasions = [o for o, v in self._occasion_vars.items() if v.get()]
        note_sentiment = {}
        loved_notes, disliked_notes = [], []
        for note, var in self._note_like_vars.items():
            val = var.get()
            if val == "Like":
                note_sentiment[note] = "like"
                loved_notes.append(note)
            elif val == "Dislike":
                note_sentiment[note] = "dislike"
                disliked_notes.append(note)
        loved_notes += self._extra_loved.get_tags()
        disliked_notes += self._extra_disliked.get_tags()
        profile = {
            "loved_families": loved_fams,
            "disliked_families": disliked_fams,
            "preferred_mediums": pref_mediums,
            "preferred_strength": self._strength_var.get(),
            "occasions": occasions,
            "note_sentiment": note_sentiment,
            "loved_notes": list(set(loved_notes)),
            "disliked_notes": list(set(disliked_notes)),
        }
        save_profile(profile)
        self._status_lbl.configure(
            text="Profile saved! Predictions now reflect your taste.",
            text_color=("#437a22", "#6daa45"),
        )
        if self._on_save:
            self._on_save()
