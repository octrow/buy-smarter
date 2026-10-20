> **Decision: PySide6** ([ADR 0001](adr/0001-desktop-gui-pyside6.md), confirmed 2026-10-20). The notes below are the
> background research that was weighed; NiceGUI, Tauri and Flet were not chosen.

https://claude.ai/chat/27aa9d21-eaf6-41db-8032-230bc472f441

Short version: since you're already on NiceGUI, you likely don't need to switch frameworks. Wrapping it as a desktop app is the fastest route. A rewrite only pays off for specific reasons, covered below.

## Option A: Keep NiceGUI, ship it as a desktop app (lowest cost)

| Path | How it works | Ubuntu notes | Effort | When to choose |
|---|---|---|---|---|
| **NiceGUI native mode** | `ui.run(native=True)` opens a pywebview window instead of a browser tab | pywebview needs the GTK/WebKit2GTK or Qt backend installed; native mode on Ubuntu has had multiprocessing quirks in the past | ~0 | Internal tools, kiosk apps |
| **nicegui-pack** (PyInstaller) | Bundles everything into one executable | Build on Ubuntu for Ubuntu (no cross-compiling) | Low | Users who shouldn't need Python installed |
| **PyTauri + NiceGUI** | Tauri 2 shell (Rust + system webview) with Python bindings | Uses WebKitGTK on Linux | Medium | You want a smaller app, auto-updater, tray icon, signed installers |
| **Tauri 2 + Python sidecar** | Tauri launches your Python server as a subprocess | Same as above | Medium | You want a fully decoupled setup |

Native mode works by calling ui.run(native=True), which is handy for kiosk apps or distributing to users who expect a desktop experience. NiceGUI is actively maintained: the current release on PyPI is 3.12.1, and v3.4 added support for pywebview 6 and forwards --clean and --noconfirm to PyInstaller in nicegui-pack.

PyTauri is the newer, more interesting option. It's a project providing Python bindings for Tauri, a cross-platform webview GUI library, and it ships an examples/nicegui-app. With the pytauri-wheel package you don't need the Rust compiler, so everything can be done in Python. One caveat: it's community-run and not officially endorsed by the Tauri project, so check how mature it is before betting a product on it.

The sidecar approach is a proven pattern: a Tauri v2 app spawns a Python subprocess that starts a FastAPI server. Because NiceGUI runs on FastAPI, it fits this directly. The trade-off for both Tauri options is rendering: Tauri uses system WebViews, so your app may render slightly differently on Windows (Chromium), macOS (WebKit), and Linux (WebKitGTK).

## Option B: Rewrite in another framework (all open source)

| Framework | Rendering | License | Mobile | Strength | Weakness vs. NiceGUI |
|---|---|---|---|---|---|
| **PySide6 (Qt 6)** | Native Qt widgets | LGPL | Limited | Most mature for serious desktop apps: tables, docking, charts | Full rewrite; imperative widget model |
| **Flet** | Flutter | Apache 2.0 | Yes | One codebase for desktop, web, and mobile | Large runtime; different component model |
| **Toga (BeeWare)** | OS-native widgets | BSD | Yes | Truly native look and feel | Smaller widget set |
| **Reflex** | Web (React) | Apache 2.0 | Web only | Full-stack Python web apps | Same category as NiceGUI, so little reason to switch |
| **Tkinter / CustomTkinter** | Tk | PSF/MIT | No | Zero dependencies | Dated; weak for complex UIs |

The 2026 guides broadly agree. PySide6, the official Python binding for Qt 6, is ranked as the strongest all-around choice for substantial desktop software, while Flet is the most approachable when one Python codebase must reach desktop, web, and mobile. They also note that for browser-based internal tools, NiceGUI usually offers a shorter path than a desktop toolkit. That supports staying where you are unless your requirements change.

## Decision matrix

| Your situation | Recommendation |
|---|---|
| Web app, sometimes needs a desktop window | NiceGUI native mode + nicegui-pack |
| Distributable product needing installers, updater, tray | PyTauri (or Tauri sidecar) wrapping NiceGUI |
| Heavy desktop UX: large tables, docking, offline, OS integration | Rewrite in PySide6 |
| Also need Android/iOS | Flet |
| Strict native look on each OS | Toga |

On Ubuntu specifically, test native mode early: install `python3-gi` + `gir1.2-webkit2-4.1`, or use the Qt backend via `pywebview[qt]`. Linux webview setup is where most surprises show up.

What's driving the question: a desktop distribution need, performance, mobile, or dissatisfaction with NiceGUI itself? The answer changes whether Option A is enough.

---

https://flet.dev/docs
Flet is a Python framework for building web, desktop, and mobile apps without prior experience in frontend development. Use ready-made controls to create your interface, connect your Python libraries and application logic, and handle user interactions without writing HTML, CSS, or JavaScript.
Start with a UI for a script or build a complete application with multiple screens. Develop with hot reload, try your app on your phone, and package it for Windows, macOS, Linux, iOS, or Android, or publish it to the web.

---

https://doc.qt.io/qtforpython-6/
Qt for Python offers the official Python bindings for Qt, which enables you to use Python to write your Qt applications. The project has two main components:
PySide6, so that you can use Qt6 APIs in your Python applications, and
Shiboken6, a binding generator tool, which can be used to expose C++ projects to Python, and a Python module with some utility functions.
Porting from PySide2 to PySide6 provides information on porting existing PySide2 applications.
This project is available under the LGPLv3/GPLv3 and the Qt commercial license.
https://doc.qt.io/qtforpython-6/tools/pyside-project.html
pyside6-project is a command line tool for creating, building and deploying Qt for Python applications. It operates on project files which are also supported by Qt Creator.
A project file contains a list of the source files used in the project. Typically they are .py, .qml, .qrc, .ts, or .ui files. It can also include other subproject files. Generated files such as compiled resources or compiled translations should not be included in the project file.
Currently, two project file formats are supported. Since PySide6 version 6.9.0, the *.pyproject file format is deprecated in favor of the new pyproject.toml file format. The *.pyproject file format is still supported for backward compatibility, but it is recommended to migrate to the new pyproject.toml file format. See Migrating from *.pyproject to pyproject.toml for more information.
https://doc.qt.io/
Welcome to the documentation pages for Qt, the cross-platform software development framework.
https://habr.com/ru/articles/909118/
Qt for Python: PySide6

---

https://v2.tauri.app/start/
Tauri is a framework for building tiny, fast binaries for all major desktop and mobile platforms. Developers can integrate any frontend framework that compiles to HTML, JavaScript, and CSS for building their user experience while leveraging languages such as Rust, Swift, and Kotlin for backend logic when needed.
Get started building with create-tauri-app by using one of the below commands. Be sure to follow the prerequisites guide to install all of the dependencies required by Tauri. For a more detailed walk through, see Create a Project

---

https://v3.tauri.app/start/
https://v3.tauri.app/start/migrate/from-tauri-2/

---

https://github.com/reflex-dev/reflex
Reflex is a library to build full-stack web apps in pure Python.
https://reflex.dev/docs/getting-started/introduction/
Reflex lets you build and deploy full-stack web apps — frontend, backend, and database — in pure Python. No JavaScript, no separate API, no context switching.

---

https://github.com/reactive-python/reactpy
It's React, but in Python
ReactPy is a library for building user interfaces in Python without Javascript. ReactPy interfaces are made from components that look and behave similar to those found in ReactJS. Designed with simplicity in mind, ReactPy can be used by those without web development experience while also being powerful enough to grow with your ambitions.
https://reactpy.dev/docs/index.html
ReactPy is a library for building user interfaces in Python without Javascript. ReactPy interfaces are made from components which look and behave similarly to those found in ReactJS. Designed with simplicity in mind, ReactPy can be used by those without web development experience while also being powerful enough to grow with your ambitions.

---

https://github.com/electron/electron
build cross-platform desktop apps with JavaScript, HTML, and CSS
https://www.electronjs.org/docs/latest/
Electron is a framework for building desktop applications using JavaScript, HTML, and CSS. By embedding Chromium and Node.js into its binary, Electron allows you to maintain one JavaScript codebase and create cross-platform apps that work on Windows, macOS, and Linux — no native development experience required.

---

Yes, React is a good choice for what you're prioritizing. The bigger decision is which **desktop shell** to use, and on Ubuntu that choice matters more than anywhere else.

## Recommended architecture

```
┌─────────────────────────────────────────────┐
│ Desktop shell (Electron)                    │
│  ┌───────────────────────────────────────┐  │
│  │ React + TypeScript + Vite             │  │
│  │ shadcn/ui + Tailwind, TanStack Query  │  │
│  └──────────────┬────────────────────────┘  │
│                 │ HTTP + WebSocket          │
│  ┌──────────────▼────────────────────────┐  │
│  │ Python sidecar: FastAPI (your logic)  │  │
│  │ packaged with PyInstaller / Nuitka    │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
        Same frontend can also run as a web app
```

You keep all your Python and replace only the presentation layer. NiceGUI already runs on FastAPI, so the backend side of the migration is mostly exposing endpoints rather than rewriting logic.

## The key choice: Electron vs Tauri on Ubuntu

| Criterion | Electron | Tauri 2 | Tauri 3 (CEF runtime) |
|---|---|---|---|
| Rendering engine on Linux | Bundled Chromium | System WebKitGTK | Bundled Chromium |
| Rendering consistency across OSes | Identical everywhere | Differs per OS | Identical |
| Performance on Ubuntu | Excellent | Weakest platform | Good (expected) |
| Stability on Ubuntu | Very mature | Known issues | Alpha |
| App size | ~100–150 MB | ~10 MB | Large (Chromium) |
| Second language | None (JS/TS) | Rust (minimal) | Rust (minimal) |
| Packaging | AppImage, .deb, snap via electron-builder | .deb, AppImage, rpm | .deb (still being fixed) |

Linux is Tauri's weak spot. One of the Tauri contributors wrote that using Tauri or any similar framework built on WebKitGTK is not a good idea for projects where Linux is a serious target, and that they actively warn people about this. Tauri is working on a Chromium-based runtime, but it's early. tauri-runtime-cef v3.0.0-alpha.0 was released September 13, and it includes fixes for CEF failing to start on Linux. Third-party Linux CEF builds have had problems too, such as a .deb that wasn't runnable after install and startup panics during GTK tray setup.

**Verdict:** you want stability and speed with Ubuntu as the main target, so **Electron is the pragmatic choice today**. The larger download buys you a single, well-tested Chromium that renders the same on every machine. Revisit Tauri once its CEF runtime reaches stable.

## Frontend stack for a nice design

| Layer | Pick | Why |
|---|---|---|
| Build | Vite + TypeScript | Fast dev server, standard in 2026 |
| Components | **shadcn/ui** (Radix + Tailwind) | Modern look, accessible, you own the code so it's fully customizable |
| Alternative | Mantine | More built-in components (dates, notifications, forms) if you'd rather not assemble pieces |
| Server state | TanStack Query | Caching, retries, background refresh |
| Tables | TanStack Table | Handles large datasets with virtualization |
| Client state | Zustand | Minimal and simple |
| API types | `openapi-typescript` or Orval | Generates TS types and client from FastAPI's OpenAPI schema, so frontend and backend stay in sync |
| Charts | Recharts or ECharts | ECharts if you used `ui.echart` in NiceGUI |

The OpenAPI codegen step is what gives you stability in practice. When a Pydantic model changes, the TypeScript build fails instead of the UI breaking at runtime.

## Migration plan

| Step | Action | Result |
|---|---|---|
| 1 | Move business logic out of NiceGUI pages into a service layer | UI-independent Python core |
| 2 | Expose it as FastAPI REST endpoints, plus WebSocket for live updates | Typed API with an OpenAPI schema |
| 3 | Run NiceGUI and the new API in the same FastAPI app | Old UI keeps working during the migration |
| 4 | Build the React app page by page against the generated client | Incremental rewrite |
| 5 | Wrap in Electron; launch the Python backend as a sidecar on a random localhost port | Desktop app |
| 6 | Package with electron-builder (AppImage + .deb), backend with PyInstaller or Nuitka | Installable Ubuntu build |
| 7 | Remove NiceGUI | Done |
Ф
## The non-React alternative, for completeness

If you'd rather stay in a single language, **ые with QML** gives native GPU-accelerated speed, a modern declarative UI, excellent Ubuntu stability, and no JS toolchain. The cost is a smaller design ecosystem than React, so a polished look takes more custom work. Since you named design quality first, React + Electron is the stronger fit.