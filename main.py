import tkinter as tk

from idat_sync import IDATSync


class MainApp(tk.Tk):
    def __init__(self):
        tk.Tk.__init__(self)
        self.frame = LoginFrame(self)
        self.frame.pack()

    def center_window(self, window_width, window_height):
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        center_x = int(screen_width / 2 - window_width / 2)
        center_y = int(screen_height / 2 - window_height / 2)
        self.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")

    def change(self, frame):
        self.frame.pack_forget()  # delete currrent frame
        self.frame = frame(self)
        self.frame.pack()  # make new frame


class LoginFrame(tk.Frame):
    def __init__(self, master, **kwargs):
        tk.Frame.__init__(self, master, **kwargs)

        # Center the window
        window_width = 300
        window_height = 150
        master.center_window(window_width, window_height)
        master.title("IDAT Sync Login")

        tk.Label(self, text="Username:").pack(pady=5)
        self.user_entry = tk.Entry(self, width=30)
        self.user_entry.pack()
        self.user_entry.focus()

        tk.Label(self, text="Password:").pack(pady=5)
        self.pass_entry = tk.Entry(self, show="*", width=30)
        self.pass_entry.pack()
        self.pass_entry.bind("<Return>", self.do_login)

        login_button = tk.Button(self, text="Login", command=self.do_login)
        login_button.pack(pady=10)

    def do_login(self, event=None):
        if (user := self.user_entry.get()) and (password := self.pass_entry.get()):
            try:
                idat_sync = IDATSync(user, password)
                self.master.change(SyncFrame)
            except:
                ...
        else:
            ...


class SyncFrame(tk.Frame):
    def __init__(self, master, **kwargs):
        tk.Frame.__init__(self, master, **kwargs)
        window_width = 600
        window_height = 400
        master.center_window(window_width, window_height)
        master.title("IDAT Sync")

        lbl = tk.Label(self, text="You made it to the main application")
        lbl.pack()


if __name__ == "__main__":
    app = MainApp()
    app.mainloop()


def main():
    user, password = get_credentials_gui()
    idat_sync = IDATSync(user, password)
    courses = idat_sync.list_my_courses()
    for chosen in courses:
        if (cid := chosen.get("id")) and (course_name := chosen.get("fullname")):
            print(
                f"\nFetching contents for: {chosen.get('fullname') or chosen.get('shortname')} (id={cid}) ..."
            )
            contents = get_course_contents(idat_sync.token, cid)
            if isinstance(contents, dict) and contents.get("exception"):
                print("[!] Error:", contents)
                return
            assignments_courses = call_ws(
                idat_sync.token, "mod_assign_get_assignments"
            ).get("courses", [])
            assignments = (
                next(
                    (
                        course
                        for course in assignments_courses
                        if course.get("id") == cid
                    ),
                    None,
                )
                or {}
            ).get("assignments", [])
            course_folder = os.path.join(ROOT_FOLDER, safe_filename(course_name))

            idat_sync.sync_courses(contents, assignments, course_name, course_folder)
