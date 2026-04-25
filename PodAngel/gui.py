from functions import *
from pathlib import Path
import customtkinter as ctk

if __name__ == "__main__":
    ensure_multiprocessing_safe()
    
    ctk.set_appearance_mode("dark")
    app = ctk.CTk()
    app.attributes("-fullscreen", True)
    app.title("PodAngel")
    app.geometry("400x300")
    app.configure(fg_color="#111111")


    top_bar = ctk.CTkFrame(app, fg_color="#1e1e1e", corner_radius=16, border_width=1, border_color="#333333")
    top_bar.pack(pady=16, padx=16, fill="x")

    top_bar.columnconfigure(0, weight=0)
    top_bar.columnconfigure(1, weight=0)
    top_bar.columnconfigure(2, weight=0)
    top_bar.columnconfigure(3, weight=1)
    top_bar.columnconfigure(4, weight=0)

    tab_view = TabView(master=app)
    tab_view.pack(pady=(0, 16), padx=16, fill="both", expand=True)
    search_field = SearchEntry(tab_view.tab("Search"))
    search_field.pack(pady=10, padx=10, fill="both", expand=True)

    library_field = LibraryEntry(tab_view.tab("Library"))
    library_field.pack(pady=10, padx=10, fill="both", expand=True)

    settings_field = SettingsEntry(tab_view.tab("Settings"))
    settings_field.pack(pady=10, padx=10, fill="both", expand=True)

    tab_view._library_entry = library_field


    search_button = TabButton(top_bar, tab_view, "Search")
    library_button = TabButton(top_bar, tab_view, "Library")
    settings_button = TabButton(top_bar, tab_view, "Settings")
    quit_button = QuitButton(top_bar, app)

    search_button.grid(row=0, column=0, padx=(10, 0), pady=8, sticky="nw")
    library_button.grid(row=0, column=1, padx=0, pady=8, sticky="nw")
    settings_button.grid(row=0, column=2, padx=0, pady=8, sticky="nw")
    quit_button.grid(row=0, column=4, padx=(5, 10), pady=8)


    tab_view.register_button("Search", search_button)
    tab_view.register_button("Library", library_button)
    tab_view.register_button("Settings", settings_button)


    tab_view.set("Search")

    # Cleanup. Destroys workers, empties the queue(Input), and closes the app.
    def _on_app_close():
        try:
            cleanup_queue()
        except Exception:
            pass
        try:
            cleanup_workers()
        except Exception:
            pass
        try:
            app.destroy()
        except Exception:
            try:
                import sys
                sys.exit(0)
            except Exception:
                import os
                os._exit(0)

    app.protocol("WM_DELETE_WINDOW", _on_app_close)
    app.mainloop()