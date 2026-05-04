import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from types import SimpleNamespace
from typing import Optional

from .cli import (
    empty_gps_provider,
    one_line,
    run_logger,
    simulated_gps_provider,
    simulated_snapshot_provider,
)
from .gps import GpsReader
from .obd import ObdError, ObdSerial, available_ports, format_supported_pids
from .windows_location import WindowsLocationError, WindowsLocationReader


GPS_WINDOWS = "Windows Location"
GPS_SERIAL = "GPS COM Port"
GPS_NONE = "No GPS"


class LoggerWorker(threading.Thread):
    def __init__(self, settings, events: queue.Queue, stop_event: threading.Event) -> None:
        super().__init__(name="obd2-gui-logger", daemon=True)
        self.settings = settings
        self.events = events
        self.stop_event = stop_event

    def run(self) -> None:
        gps_reader = None
        windows_location_reader = None

        try:
            args = self._make_logger_args()
            gps_provider = empty_gps_provider()

            if self.settings.simulate and self.settings.gps_mode == GPS_NONE:
                gps_provider = simulated_gps_provider()

            if self.settings.gps_mode == GPS_WINDOWS:
                self._message("Using Windows Location Services...")
                windows_location_reader = WindowsLocationReader(
                    poll_interval_s=args.interval,
                    maximum_age_s=self.settings.windows_location_maximum_age,
                    timeout_s=self.settings.windows_location_timeout,
                    desired_accuracy_m=self.settings.windows_location_accuracy_m,
                )
                windows_location_reader.start()
                gps_provider = windows_location_reader.latest
            elif self.settings.gps_mode == GPS_SERIAL:
                self._message(f"Opening GPS port {self.settings.gps_port}...")
                gps_reader = GpsReader(self.settings.gps_port, self.settings.gps_baud)
                gps_reader.start()
                gps_provider = gps_reader.latest

            if self.settings.simulate:
                code = run_logger(
                    args,
                    simulated_snapshot_provider(),
                    gps_provider,
                    should_stop=self.stop_event.is_set,
                    on_message=self._message,
                    on_row=self._row,
                )
                self._done(code)
                return

            self._message(f"Opening OBD port {self.settings.obd_port}...")
            with ObdSerial(self.settings.obd_port, baudrate=self.settings.baud) as obd:
                self._message("Initializing ELM327...")
                init_responses = obd.initialize()
                for command, response in init_responses.items():
                    self._message(f"{command}: {one_line(response)}")

                supported = obd.supported_pids()
                self._message(f"Supported PIDs: {format_supported_pids(supported)}")
                if supported is not None and 0x5E not in supported:
                    self._message(
                        "Warning: PID 015E fuel rate is not supported. "
                        "Fuel totals may stay blank unless MAF estimate is enabled."
                    )

                provider = lambda: obd.read_snapshot(supported)
                code = run_logger(
                    args,
                    provider,
                    gps_provider,
                    should_stop=self.stop_event.is_set,
                    on_message=self._message,
                    on_row=self._row,
                )
                self._done(code)
        except (ObdError, WindowsLocationError, OSError, ValueError) as exc:
            self._error(str(exc))
        except Exception as exc:  # pragma: no cover - defensive GUI boundary
            self._error(f"Unexpected error: {exc}")
        finally:
            if gps_reader is not None:
                gps_reader.stop()
            if windows_location_reader is not None:
                windows_location_reader.stop()

    def _make_logger_args(self):
        return SimpleNamespace(
            out_dir=self.settings.out_dir,
            state_file=self.settings.state_file,
            fuel_price=self.settings.fuel_price,
            samples=None,
            print_csv_row=self.settings.print_csv_row,
            interval=self.settings.interval,
            allow_maf_fuel_estimate=self.settings.allow_maf_fuel_estimate,
            assumed_diesel_afr=28.0,
            diesel_density_g_l=832.0,
            diesel_energy_mj_l=35.8,
            engine_efficiency=0.35,
            maf_hp_factor=1.08,
            fallback_baro_kpa=101.325,
        )

    def _message(self, text: str) -> None:
        self.events.put(("message", text))

    def _row(self, row: dict) -> None:
        self.events.put(("row", row))

    def _done(self, code: int) -> None:
        self.events.put(("done", code))

    def _error(self, text: str) -> None:
        self.events.put(("error", text))


class ObdLoggerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("OBD2 Logger")
        self.geometry("1040x720")
        self.minsize(900, 620)

        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: Optional[LoggerWorker] = None
        self.current_csv: Optional[str] = None
        self.close_after_stop = False

        self._init_vars()
        self._build_ui()
        self.refresh_ports()
        self._poll_events()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _init_vars(self) -> None:
        self.obd_port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value="38400")
        self.gps_mode_var = tk.StringVar(value=GPS_WINDOWS)
        self.gps_port_var = tk.StringVar()
        self.gps_baud_var = tk.StringVar(value="9600")
        self.simulate_var = tk.BooleanVar(value=False)

        self.interval_var = tk.StringVar(value="1.0")
        self.fuel_price_var = tk.StringVar(value="1.50")
        self.out_dir_var = tk.StringVar(value=os.path.abspath("logs"))
        self.state_file_var = tk.StringVar(value=os.path.abspath("state.json"))
        self.maf_estimate_var = tk.BooleanVar(value=True)
        self.print_csv_var = tk.BooleanVar(value=False)

        self.status_var = tk.StringVar(value="Ready")
        self.csv_var = tk.StringVar(value="-")
        self.speed_var = tk.StringVar(value="-")
        self.rpm_var = tk.StringVar(value="-")
        self.engine_load_var = tk.StringVar(value="-")
        self.fuel_rate_var = tk.StringVar(value="-")
        self.gps_var = tk.StringVar(value="-")

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self, padding=14)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.columnconfigure(1, weight=1)

        main = ttk.Frame(self, padding=(0, 14, 14, 14))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        self._build_connection_frame(sidebar)
        self._build_logging_frame(sidebar)
        self._build_actions(sidebar)
        self._build_status_cards(main)
        self._build_live_log(main)

    def _build_connection_frame(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Connection", padding=12)
        frame.grid(row=0, column=0, columnspan=2, sticky="ew")
        frame.columnconfigure(1, weight=1)

        ttk.Checkbutton(
            frame,
            text="Simulate OBD",
            variable=self.simulate_var,
            command=self._update_control_states,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        ttk.Label(frame, text="OBD port").grid(row=1, column=0, sticky="w", pady=3)
        self.obd_port_combo = ttk.Combobox(frame, textvariable=self.obd_port_var, width=28)
        self.obd_port_combo.grid(row=1, column=1, sticky="ew", pady=3)

        ttk.Label(frame, text="OBD baud").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.baud_var, width=12).grid(
            row=2, column=1, sticky="ew", pady=3
        )

        ttk.Label(frame, text="GPS source").grid(row=3, column=0, sticky="w", pady=3)
        self.gps_mode_combo = ttk.Combobox(
            frame,
            textvariable=self.gps_mode_var,
            values=[GPS_WINDOWS, GPS_SERIAL, GPS_NONE],
            state="readonly",
            width=28,
        )
        self.gps_mode_combo.grid(row=3, column=1, sticky="ew", pady=3)
        self.gps_mode_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_control_states())

        ttk.Label(frame, text="GPS port").grid(row=4, column=0, sticky="w", pady=3)
        self.gps_port_combo = ttk.Combobox(frame, textvariable=self.gps_port_var, width=28)
        self.gps_port_combo.grid(row=4, column=1, sticky="ew", pady=3)

        ttk.Label(frame, text="GPS baud").grid(row=5, column=0, sticky="w", pady=3)
        self.gps_baud_entry = ttk.Entry(frame, textvariable=self.gps_baud_var, width=12)
        self.gps_baud_entry.grid(row=5, column=1, sticky="ew", pady=3)

        ttk.Button(frame, text="Refresh ports", command=self.refresh_ports).grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0)
        )

    def _build_logging_frame(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Logging", padding=12)
        frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Interval sec").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.interval_var, width=12).grid(
            row=0, column=1, sticky="ew", pady=3
        )

        ttk.Label(frame, text="Fuel price/L").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.fuel_price_var, width=12).grid(
            row=1, column=1, sticky="ew", pady=3
        )

        ttk.Label(frame, text="Output dir").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.out_dir_var).grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Button(frame, text="Browse", command=self._browse_out_dir).grid(
            row=3, column=1, sticky="ew", pady=(0, 6)
        )

        ttk.Label(frame, text="State file").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.state_file_var).grid(row=4, column=1, sticky="ew", pady=3)

        ttk.Checkbutton(
            frame,
            text="MAF fuel estimate",
            variable=self.maf_estimate_var,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 2))
        ttk.Checkbutton(
            frame,
            text="Show CSV rows",
            variable=self.print_csv_var,
        ).grid(row=6, column=0, columnspan=2, sticky="w")

        ttk.Button(frame, text="Reset state totals", command=self._reset_state).grid(
            row=7, column=0, columnspan=2, sticky="ew", pady=(10, 0)
        )

    def _build_actions(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        self.start_button = ttk.Button(frame, text="Start logging", command=self.start_logging)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))

        self.stop_button = ttk.Button(frame, text="Stop", command=self.stop_logging, state="disabled")
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(5, 0))

        ttk.Button(frame, text="Open logs folder", command=self._open_logs_folder).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0)
        )
        ttk.Button(frame, text="Open current CSV", command=self._open_current_csv).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )

    def _build_status_cards(self, parent: ttk.Frame) -> None:
        top = ttk.Frame(parent)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure((0, 1, 2), weight=1)

        cards = [
            ("Status", self.status_var),
            ("CSV", self.csv_var),
            ("GPS", self.gps_var),
            ("Speed", self.speed_var),
            ("RPM", self.rpm_var),
            ("Engine load", self.engine_load_var),
            ("Fuel rate", self.fuel_rate_var),
        ]

        for index, (label, var) in enumerate(cards):
            card = ttk.LabelFrame(top, text=label, padding=(10, 8))
            card.grid(row=index // 3, column=index % 3, sticky="ew", padx=4, pady=4)
            card.columnconfigure(0, weight=1)
            ttk.Label(card, textvariable=var, font=("Segoe UI", 11, "bold")).grid(
                row=0, column=0, sticky="w"
            )

    def _build_live_log(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Live log").grid(row=1, column=0, sticky="w", pady=(12, 4))
        frame = ttk.Frame(parent)
        frame.grid(row=2, column=0, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(frame, height=18, wrap="none", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")

        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=yscroll.set)

    def refresh_ports(self) -> None:
        try:
            ports = available_ports()
        except ObdError as exc:
            self._append_log(str(exc))
            ports = []

        names = [port.split(" - ", 1)[0] for port in ports]
        self.obd_port_combo.configure(values=names)
        self.gps_port_combo.configure(values=names)

        if names:
            if not self.obd_port_var.get():
                self.obd_port_var.set("COM4" if "COM4" in names else names[0])
            if not self.gps_port_var.get():
                gps_candidates = [name for name in names if name != self.obd_port_var.get()]
                self.gps_port_var.set(gps_candidates[0] if gps_candidates else names[0])

        self._append_log("Detected ports: " + (", ".join(names) if names else "none"))
        self._update_control_states()

    def start_logging(self) -> None:
        settings = self._collect_settings()
        if settings is None:
            return

        self.stop_event.clear()
        self.worker = LoggerWorker(settings, self.events, self.stop_event)
        self.worker.start()
        self.status_var.set("Starting")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self._set_inputs_state("disabled")
        self._append_log("Logger starting...")

    def stop_logging(self) -> None:
        if self.worker is None:
            return
        self.stop_event.set()
        self.status_var.set("Stopping")
        self.stop_button.configure(state="disabled")
        self._append_log("Stop requested. Waiting for the current OBD read to finish...")

    def _collect_settings(self):
        try:
            interval = float(self.interval_var.get())
            fuel_price = float(self.fuel_price_var.get())
            baud = int(self.baud_var.get())
            gps_baud = int(self.gps_baud_var.get())
        except ValueError:
            messagebox.showerror("Invalid setting", "Baud, interval, and fuel price must be numbers.")
            return None

        if interval <= 0:
            messagebox.showerror("Invalid setting", "Interval must be greater than 0.")
            return None

        simulate = self.simulate_var.get()
        obd_port = self.obd_port_var.get().strip()
        gps_mode = self.gps_mode_var.get()
        gps_port = self.gps_port_var.get().strip()

        if not simulate and not obd_port:
            messagebox.showerror("Missing OBD port", "Select an OBD COM port first.")
            return None
        if gps_mode == GPS_SERIAL and not gps_port:
            messagebox.showerror("Missing GPS port", "Select a GPS COM port first.")
            return None
        if gps_mode == GPS_SERIAL and gps_port == obd_port and not simulate:
            messagebox.showerror("Port conflict", "GPS port cannot be the same as the OBD port.")
            return None

        out_dir = os.path.abspath(self.out_dir_var.get().strip() or "logs")
        state_file = os.path.abspath(self.state_file_var.get().strip() or "state.json")

        return SimpleNamespace(
            simulate=simulate,
            obd_port=obd_port,
            baud=baud,
            gps_mode=gps_mode,
            gps_port=gps_port,
            gps_baud=gps_baud,
            interval=interval,
            fuel_price=fuel_price,
            out_dir=out_dir,
            state_file=state_file,
            allow_maf_fuel_estimate=self.maf_estimate_var.get(),
            print_csv_row=self.print_csv_var.get(),
            windows_location_timeout=5.0,
            windows_location_maximum_age=10.0,
            windows_location_accuracy_m=50,
        )

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "message":
                    self._handle_message(payload)
                elif event == "row":
                    self._handle_row(payload)
                elif event == "done":
                    self._finish_logging(f"Stopped ({payload})")
                elif event == "error":
                    self._append_log(payload)
                    self._finish_logging("Error")
                    messagebox.showerror("Logger error", payload)
        except queue.Empty:
            pass
        self.after(150, self._poll_events)

    def _handle_message(self, message: str) -> None:
        self._append_log(message)
        if message.startswith("Writing CSV:"):
            self.current_csv = message.split(":", 1)[1].strip()
            self.csv_var.set(os.path.basename(self.current_csv))

    def _handle_row(self, row: dict) -> None:
        self.status_var.set(row.get("timestamp", "Running"))
        self.speed_var.set(f"{row.get('obd_speed_kph')} km/h")
        self.rpm_var.set(str(row.get("rpm")))
        self.engine_load_var.set(f"{row.get('계산된 엔진 부하 (%)')} %")
        self.fuel_rate_var.set(f"{row.get('계산된 순간 연료 소비율 (L/h)')} L/h")
        self.gps_var.set(
            f"{row.get('Latitude')}, {row.get('Longitude')} ({row.get('gps_valid')})"
        )

    def _finish_logging(self, status: str) -> None:
        self.worker = None
        self.status_var.set(status)
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self._set_inputs_state("normal")
        self._update_control_states()
        self._append_log(status)
        if self.close_after_stop:
            self.destroy()

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_inputs_state(self, state: str) -> None:
        for child in self.winfo_children():
            self._set_state_recursive(child, state)
        self.start_button.configure(state="disabled" if state == "disabled" else "normal")
        self.stop_button.configure(state="normal" if self.worker is not None else "disabled")

    def _set_state_recursive(self, widget, state: str) -> None:
        if widget in (self.log_text, self.start_button, self.stop_button):
            return
        try:
            widget.configure(state=state)
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._set_state_recursive(child, state)

    def _update_control_states(self) -> None:
        if self.worker is not None:
            return

        gps_serial = self.gps_mode_var.get() == GPS_SERIAL
        simulate = self.simulate_var.get()
        self.obd_port_combo.configure(state="disabled" if simulate else "readonly")
        self.gps_port_combo.configure(state="readonly" if gps_serial else "disabled")
        self.gps_baud_entry.configure(state="normal" if gps_serial else "disabled")

    def _browse_out_dir(self) -> None:
        path = filedialog.askdirectory(initialdir=self.out_dir_var.get() or os.getcwd())
        if path:
            self.out_dir_var.set(os.path.abspath(path))

    def _reset_state(self) -> None:
        path = self.state_file_var.get().strip()
        if not path:
            return
        path = os.path.abspath(path)
        if not os.path.exists(path):
            self._append_log(f"State file not found: {path}")
            return
        if not messagebox.askyesno("Reset state totals", f"Delete this state file?\n\n{path}"):
            return
        os.remove(path)
        self._append_log(f"Deleted state file: {path}")

    def _open_logs_folder(self) -> None:
        path = os.path.abspath(self.out_dir_var.get().strip() or "logs")
        os.makedirs(path, exist_ok=True)
        os.startfile(path)

    def _open_current_csv(self) -> None:
        if self.current_csv and os.path.exists(self.current_csv):
            os.startfile(self.current_csv)
            return
        messagebox.showinfo("No CSV yet", "Start logging first, then open the current CSV.")

    def _on_close(self) -> None:
        if self.worker is not None:
            if not messagebox.askyesno("Logger is running", "Stop logging and close the GUI?"):
                return
            self.close_after_stop = True
            self.stop_event.set()
            self.status_var.set("Stopping")
            self.stop_button.configure(state="disabled")
            self._append_log("Stop requested. The window will close after the COM port is released.")
            return
        self.destroy()


def main() -> int:
    app = ObdLoggerApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
