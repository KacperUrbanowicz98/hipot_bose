import customtkinter as ctk
from config import COLORS
from login_screen import LoginScreen
from main_screen import MainScreen
from engineer_panel import EngineerPanel
from password_dialog import PasswordDialog

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("HiPot Tester — Bose Production")
        self.geometry("620x520")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg"])
        self._d_count     = 0
        self._d_reset_job = None
        self._show_login()

    def _show_login(self):
        for w in self.winfo_children():
            w.destroy()
        LoginScreen(
            self,
            on_login_success=self._on_login
        ).place(relwidth=1.0, relheight=1.0)

    def _on_login(self, hrid: str, user: dict):
        self.bind("<KeyPress>", self._on_key_press)
        for w in self.winfo_children():
            w.destroy()
        MainScreen(
            self,
            hrid=hrid,
            user=user,
            on_logout=self._on_logout
        ).place(relwidth=1.0, relheight=1.0)

    def _on_logout(self):
        self.unbind("<KeyPress>")
        self._d_count = 0
        self._show_login()

    def _on_key_press(self, event):
        ctrl  = (event.state & 0x0004) != 0
        shift = (event.state & 0x0001) != 0
        alt   = (event.state & 0x20000) != 0
        key   = event.keysym.lower() == "d"
        if ctrl and shift and alt and key:
            self._d_count += 1
            if self._d_reset_job:
                self.after_cancel(self._d_reset_job)
            self._d_reset_job = self.after(1000, self._reset_d_count)
            if self._d_count >= 3:
                self._d_count = 0
                PasswordDialog(self, on_success=lambda: EngineerPanel(self))

    def _reset_d_count(self):
        self._d_count = 0


if __name__ == "__main__":
    app = App()
    app.mainloop()