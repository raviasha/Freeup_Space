"""Downloadable Freeup Space installer; no user Python or terminal required."""
import argparse
import json
import queue
import sys
import tarfile
import tempfile
import threading
import webbrowser
from pathlib import Path
from urllib.parse import quote

from installer.bundle import extract_payload
from installer.core import SetupError, default_install_root, discover_codex, install
from installer.health import smoke_runtime

RELEASES_URL = 'https://github.com/raviasha/Freeup_Space/releases/latest'


def payload_archive():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'payload.tar.gz'


def bundle_version(archive):
    with tarfile.open(archive, 'r:gz') as tar:
        return json.load(tar.extractfile('plugin/.codex-plugin/plugin.json'))['version'].split('+')[0]


def verify_package(archive, report_path):
    try:
        with tempfile.TemporaryDirectory(prefix='freeup-setup-check-') as directory:
            payload = Path(directory)
            extract_payload(archive, payload)
            executable = payload / 'runtime' / ('freeup-space.exe' if sys.platform == 'win32' else 'freeup-space')
            result = smoke_runtime(executable)
    except Exception as error:
        result = {'ok': False, 'error': str(error)}
    report_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    return 0 if result['ok'] else 1


def show_setup(archive):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title('Freeup Space Setup')
    root.geometry('600x440')
    root.minsize(550, 440)
    root.configure(background='#f4f7f4')
    style = ttk.Style(root)
    style.configure('TFrame', background='#f4f7f4')
    style.configure('TLabel', background='#f4f7f4', foreground='#192522')
    style.configure('Title.TLabel', font=('Arial', 25, 'bold'))
    style.configure('TButton', padding=(14, 9))
    frame = ttk.Frame(root, padding=28)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Freeup Space', style='Title.TLabel').pack(anchor='w')
    ttk.Label(frame, text=f'Setup {bundle_version(archive)}', padding=(0, 4, 0, 18)).pack(anchor='w')
    ttk.Label(frame, text='Get ready to scan, review, and reclaim space.\nEverything the plugin needs is included.',
              wraplength=520, justify='left').pack(anchor='w')
    status = tk.StringVar(value='Checking for Codex…')
    ttk.Label(frame, textvariable=status, wraplength=520, justify='left', padding=(0, 22, 0, 12)).pack(anchor='w')
    bar = ttk.Progressbar(frame, mode='indeterminate')
    bar.pack(fill='x', pady=(0, 16))
    events = queue.Queue()
    context = {'busy': True, 'codex': None, 'result': None, 'log': [], 'destination': default_install_root()}
    actions = ttk.Frame(frame)
    actions.pack(fill='x')

    def log_message(message):
        context['log'].append(str(message))
        status.set(str(message))

    def work():
        try:
            with tempfile.TemporaryDirectory(prefix='freeup-space-setup-') as directory:
                events.put(('progress', 'Preparing the included runtime…'))
                payload = Path(directory)
                extract_payload(archive, payload)
                result = install(payload, context['destination'], Path(context['codex']),
                                 progress=lambda message: events.put(('progress', message)))
                events.put(('ready', result))
        except Exception as error:
            events.put(('error', str(error)))

    def start():
        if context['busy'] or not context['codex']:
            return
        context['busy'] = True
        install_button.configure(state='disabled')
        browse_button.configure(state='disabled')
        open_button.configure(state='disabled')
        bar.start(12)
        threading.Thread(target=work, daemon=False).start()

    def choose_codex():
        selected = filedialog.askopenfilename(title='Choose the Codex executable')
        if selected:
            context['codex'] = selected
            log_message('Codex selected. Ready to install or repair Freeup Space.')
            install_button.configure(state='normal')

    def open_plugin():
        result = context['result']
        if result:
            path = quote(str(result['marketplace_path']), safe='')
            webbrowser.open('codex://plugins/freeup-space?marketplacePath='+path)

    install_button = ttk.Button(actions, text='Install / Update / Repair', command=start, state='disabled')
    install_button.pack(side='left')
    open_button = ttk.Button(actions, text='Open in Codex', command=open_plugin, state='disabled')
    open_button.pack(side='right')
    links = ttk.Frame(frame)
    links.pack(fill='x', pady=(16, 8))
    browse_button = ttk.Button(links, text='Choose Codex…', command=choose_codex, state='disabled')
    browse_button.pack(side='left')
    ttk.Button(links, text='Check for updates', command=lambda: webbrowser.open(RELEASES_URL)).pack(side='right')

    def show_log():
        dialog = tk.Toplevel(root)
        dialog.title('Setup details')
        text = tk.Text(dialog, width=90, height=22, wrap='word')
        text.pack(fill='both', expand=True)
        text.insert('1.0', 'Setup folder: '+str(context['destination'])+'\n\n'+('\n\n'.join(context['log']) or 'No setup actions yet.'))
        text.configure(state='disabled')

        def choose_folder():
            selected = filedialog.askdirectory(parent=dialog, title='Choose an empty folder dedicated to Freeup Space',
                                               initialdir=str(context['destination'].parent))
            if selected:
                context['destination'] = Path(selected)
                log_message('Setup folder selected. Click Install / Update / Repair to retry.')
                dialog.destroy()

        ttk.Button(dialog, text='Choose setup folder…', command=choose_folder,
                   state='disabled' if context['busy'] else 'normal').pack(padx=12, pady=12, anchor='w')

    ttk.Button(frame, text='Setup details', command=show_log).pack(anchor='w')

    def discover():
        try:
            events.put(('host', discover_codex()))
        except Exception as error:
            events.put(('error', 'Could not locate Codex: '+str(error)))

    def drain_events():
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == 'progress':
                    log_message(value)
                    continue
                context['busy'] = False
                bar.stop()
                browse_button.configure(state='normal')
                if kind == 'host':
                    context['codex'] = str(value[0]) if value else None
                    log_message('Codex found. Ready to install or repair Freeup Space.' if value else
                                'Codex was not found. Install and open the Codex desktop app, then reopen setup. If it is already installed, use Choose Codex.')
                elif kind == 'ready':
                    context['result'] = value
                    warnings = value.get('warnings', [])
                    if warnings:
                        log_message('Installed, with something to check: '+' '.join(warnings)+' See Setup details.')
                    else:
                        log_message('Ready! Open a new Codex task and ask “Open Freeup Space”.')
                    open_button.configure(state='normal')
                else:
                    log_message('Setup needs attention: '+str(value)+' See Setup details for recovery options.')
                install_button.configure(state='normal' if context['codex'] else 'disabled')
        except queue.Empty:
            pass
        root.after(100, drain_events)

    def close():
        if context['busy']:
            messagebox.showinfo('Setup is running', 'Please leave this window open until setup finishes.')
        else:
            root.destroy()

    root.protocol('WM_DELETE_WINDOW', close)
    threading.Thread(target=discover, daemon=True).start()
    root.after(100, drain_events)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, default=payload_archive(), help=argparse.SUPPRESS)
    parser.add_argument('--smoke-test', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.smoke_test:
        return verify_package(args.payload, args.smoke_test)
    show_setup(args.payload)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
