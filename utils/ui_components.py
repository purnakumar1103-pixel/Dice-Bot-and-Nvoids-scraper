import tkinter as tk

class ToggleSwitch(tk.Canvas):
    def __init__(self, parent, variable=None, command=None,
                 bg="#252b3b", on_color="#34c759", off_color="#3b4252",
                 handle_color="#ffffff", width=40, height=22, **kwargs):
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0, bd=0, **kwargs)
        self.variable = variable
        self.command = command
        
        self.on_color = on_color
        self.off_color = off_color
        self.handle_color = handle_color
        
        self.width = width
        self.height = height
        self.radius = height // 2
        
        # State
        self.is_on = False
        if self.variable is not None:
            try:
                self.is_on = bool(self.variable.get())
            except Exception:
                pass
                
        # Draw background track
        self.track = self._create_rounded_rect(0, 0, width, height, self.radius, 
                                               fill=self.on_color if self.is_on else self.off_color)
        
        # Draw handle
        handle_padding = 2
        handle_x = width - height + handle_padding if self.is_on else handle_padding
        self.handle = self.create_oval(handle_x, handle_padding, 
                                       handle_x + height - 2*handle_padding, height - handle_padding, 
                                       fill=self.handle_color, outline="")
        
        # Bind click
        self.bind("<Button-1>", self.toggle)
        self.bind("<Enter>", lambda e: self.config(cursor="hand2"))
        
        # Watch variable if provided
        if self.variable is not None:
            self.variable.trace_add("write", self._on_var_change)

    def _create_rounded_rect(self, x1, y1, x2, y2, radius=25, **kwargs):
        points = [x1+radius, y1, x1+radius, y1, x2-radius, y1, x2-radius, y1,
                  x2, y1, x2, y1+radius, x2, y1+radius, x2, y2-radius, x2, y2-radius,
                  x2, y2, x2-radius, y2, x2-radius, y2, x1+radius, y2, x1+radius, y2,
                  x1, y2, x1, y2-radius, x1, y2-radius, x1, y1+radius, x1, y1+radius, x1, y1]
        return self.create_polygon(points, smooth=True, **kwargs)

    def toggle(self, event=None):
        self.is_on = not self.is_on
        if self.variable is not None:
            self.variable.set(self.is_on)
        self._animate()
        if self.command:
            self.command()

    def _on_var_change(self, *args):
        new_state = bool(self.variable.get())
        if self.is_on != new_state:
            self.is_on = new_state
            self._animate()

    def _animate(self):
        handle_padding = 2
        target_x = self.width - self.height + handle_padding if self.is_on else handle_padding
        current_x = self.coords(self.handle)[0]
        
        # Change color immediately
        self.itemconfig(self.track, fill=self.on_color if self.is_on else self.off_color)
        
        # Simple step animation
        step = (target_x - current_x) / 5
        self._animate_step(current_x, target_x, step, 5)

    def _animate_step(self, current_x, target_x, step, frames_left):
        if frames_left <= 0:
            handle_padding = 2
            final_x = self.width - self.height + handle_padding if self.is_on else handle_padding
            self.coords(self.handle, final_x, handle_padding, final_x + self.height - 2*handle_padding, self.height - handle_padding)
            return
            
        new_x = current_x + step
        handle_padding = 2
        self.coords(self.handle, new_x, handle_padding, new_x + self.height - 2*handle_padding, self.height - handle_padding)
        self.after(10, lambda: self._animate_step(new_x, target_x, step, frames_left - 1))


class LabeledToggleSwitch(tk.Frame):
    def __init__(self, parent, text="", variable=None, command=None, bg="#252b3b", fg="#ffffff", font=None, **kwargs):
        super().__init__(parent, bg=bg)
        
        self.switch = ToggleSwitch(self, variable=variable, command=command, bg=bg, **kwargs)
        self.switch.pack(side=tk.LEFT)
        
        self.label = tk.Label(self, text=text, bg=bg, fg=fg, font=font)
        self.label.pack(side=tk.LEFT, padx=(8, 0))
        
        # Allow clicking the label to toggle as well
        self.label.bind("<Button-1>", lambda e: self.switch.toggle())
        self.label.bind("<Enter>", lambda e: self.label.config(cursor="hand2"))
