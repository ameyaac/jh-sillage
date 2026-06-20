import customtkinter as ctk
from ui.form_tab import FormTab
from ui.log_tab import LogTab
from ui.profile_tab import ProfileTab
from model.predictor import load_profile


class AppWindow(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("JH Sillage - Perfume Preference Predictor")
        self.geometry("980x720")
        self.minsize(820, 600)

        header = ctk.CTkFrame(
            self, height=56, corner_radius=0, fg_color=("#6a3579", "#4a1f5e")
        )
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(
            header,
            text="Sillage",
            font=ctk.CTkFont(family="Georgia", size=22, weight="bold"),
            text_color="white",
        ).pack(side="left", padx=20)
        ctk.CTkLabel(
            header,
            text="Perfume Preference Predictor",
            font=ctk.CTkFont(size=12),
            text_color="#d8b4f0",
        ).pack(side="left")

        profile = load_profile()
        status = (
            "Profile loaded"
            if profile
            else "No profile yet - fill in the Taste Profile tab first"
        )
        self._status_lbl = ctk.CTkLabel(
            header,
            text=status,
            font=ctk.CTkFont(size=10),
            text_color=("#b8f0c0" if profile else "#ffd080"),
        )
        self._status_lbl.pack(side="right", padx=16)

        self._tabs = ctk.CTkTabview(self, anchor="nw")
        self._tabs.pack(fill="both", expand=True, padx=16, pady=(12, 16))
        self._tabs.add("  Taste Profile  ")
        self._tabs.add("  Log Perfume  ")
        self._tabs.add("  My Collection  ")

        self._profile_tab = ProfileTab(
            self._tabs.tab("  Taste Profile  "), on_save=self._on_profile_saved
        )
        self._profile_tab.pack(fill="both", expand=True)

        self._log_tab = LogTab(self._tabs.tab("  My Collection  "))
        self._form_tab = FormTab(
            self._tabs.tab("  Log Perfume  "), on_save=self._on_entry_saved
        )
        self._log_tab.pack(fill="both", expand=True)
        self._form_tab.pack(fill="both", expand=True)
        self._log_tab.refresh()

        if not profile:
            self._tabs.set("  Taste Profile  ")

    def _on_profile_saved(self):
        self._status_lbl.configure(text="Profile loaded", text_color="#b8f0c0")

    def _on_entry_saved(self):
        self._log_tab.refresh()
