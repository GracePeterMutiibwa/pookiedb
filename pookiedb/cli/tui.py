from __future__ import annotations
import os
import textwrap
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    RadioButton,
    RadioSet,
    Static,
)


# ── ASCII art splash ──────────────────────────────────────────────────────────

POOKIE_ART = """\
  ██████╗  ██████╗  ██████╗ ██╗  ██╗██╗███████╗
  ██╔══██╗██╔═══██╗██╔═══██╗██║ ██╔╝██║██╔════╝
  ██████╔╝██║   ██║██║   ██║█████╔╝ ██║█████╗  
  ██╔═══╝ ██║   ██║██║   ██║██╔═██╗ ██║██╔══╝  
  ██║     ╚██████╔╝╚██████╔╝██║  ██╗██║███████╗
  ╚═╝      ╚═════╝  ╚═════╝ ╚═╝  ╚═╝╚═╝╚══════╝
"""

TAGLINE = "🐾  A Django-style ORM for PostgreSQL & SQLite"
AUTHOR = "by Grace Peter Mutiibwa"

POOKIE_CSS = """
/* ── global ── */
Screen {
    background: #0d1117;
    color: #e6edf3;
}

/* ── splash ── */
#splash-box {
    width: 80;
    height: auto;
    border: round #f78166;
    padding: 1 2;
    background: #161b22;
    margin: 1;
    align: center middle;
}

#art {
    color: #f78166;
    text-style: bold;
    width: 100%;
    content-align: center middle;
}

#tagline {
    color: #79c0ff;
    text-style: italic;
    width: 100%;
    content-align: center middle;
}

#author-label {
    color: #8b949e;
    width: 100%;
    content-align: center middle;
}

#splash-hint {
    color: #3fb950;
    margin-top: 1;
    width: 100%;
    content-align: center middle;
}

/* ── wizard ── */
#wizard-container {
    width: 76;
    height: auto;
    border: round #388bfd;
    padding: 1 3;
    background: #161b22;
    margin: 1;
    align: center middle;
}

#step-title {
    color: #f78166;
    text-style: bold;
    margin-bottom: 1;
}

#step-subtitle {
    color: #8b949e;
    margin-bottom: 1;
}

.field-label {
    color: #79c0ff;
    margin-top: 1;
}

Input {
    background: #0d1117;
    border: tall #30363d;
    color: #e6edf3;
    width: 100%;
}

Input:focus {
    border: tall #388bfd;
}

RadioSet {
    background: #161b22;
    border: none;
    padding: 0;
}

RadioButton {
    color: #e6edf3;
}

RadioButton:focus {
    color: #79c0ff;
}

/* ── buttons ── */
#btn-row {
    margin-top: 2;
    height: 3;
    align: right middle;
}

Button {
    margin-left: 1;
}

Button.primary {
    background: #238636;
    color: #e6edf3;
    border: none;
}

Button.primary:hover {
    background: #2ea043;
}

Button.secondary {
    background: #21262d;
    color: #8b949e;
    border: none;
}

Button.secondary:hover {
    background: #30363d;
    color: #e6edf3;
}

/* ── summary / done ── */
#summary-box {
    width: 76;
    height: auto;
    border: round #3fb950;
    padding: 1 3;
    background: #161b22;
    margin: 1;
    align: center middle;
}

#summary-title {
    color: #3fb950;
    text-style: bold;
    margin-bottom: 1;
}

#summary-body {
    color: #e6edf3;
}

#done-hint {
    color: #8b949e;
    margin-top: 1;
    width: 100%;
    content-align: center middle;
}

/* ── progress bar ── */
#progress-row {
    height: 1;
    width: 100%;
    margin-bottom: 1;
}

.dot-done   { color: #3fb950; }
.dot-active { color: #f78166; }
.dot-todo   { color: #30363d; }
"""

TOTAL_STEPS = 5


# ── Splash screen ─────────────────────────────────────────────────────────────


class SplashScreen(Screen):
    BINDINGS = [Binding("enter", "proceed", "Continue"), Binding("q", "quit_app", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Center():
            with Vertical(id="splash-box"):
                yield Static(POOKIE_ART, id="art")
                yield Static(TAGLINE, id="tagline")
                yield Static(AUTHOR, id="author-label")
                yield Static(
                    "Press [bold green]Enter[/] to start a new project  ·  [bold red]Q[/] to quit",
                    id="splash-hint",
                )
        yield Footer()

    def action_proceed(self):
        self.app.push_screen(WizardScreen())

    def action_quit_app(self):
        self.app.exit()


# ── Wizard screen ─────────────────────────────────────────────────────────────


class WizardScreen(Screen):
    """Multi-step project wizard."""

    BINDINGS = [Binding("escape", "back", "Back")]

    step: reactive[int] = reactive(1)

    # Collected answers
    _answers: dict = {}

    STEPS = [
        # (title, subtitle, fields)
        (
            "Step 1 / 5: Project Name",
            "Give your project a name. This becomes the root folder.",
            "project_name",
        ),
        (
            "Step 2 / 5: Database Engine",
            "Which database will you use?",
            "db_engine",
        ),
        (
            "Step 3 / 5: Database Connection",
            "Enter connection details for your database.",
            "db_details",
        ),
        (
            "Step 4 / 5: First Model",
            "Name your first model class (e.g. User, Product, Post). Leave blank to skip.",
            "first_model",
        ),
        (
            "Step 5 / 5: Author / Package Info",
            "Optional metadata written into your pyproject.toml.",
            "author_info",
        ),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with ScrollableContainer():
            with Vertical(id="wizard-container"):
                yield Static("", id="progress-row")
                yield Static("", id="step-title")
                yield Static("", id="step-subtitle")
                # ── step 1: project name ──
                with Vertical(id="fields-step-1"):
                    yield Label("Project name", classes="field-label")
                    yield Input(placeholder="my_project", id="inp-project-name")
                    yield Label("Python package name (auto-filled)", classes="field-label")
                    yield Input(placeholder="my_project", id="inp-package-name")
                # ── step 2: db engine ──
                with Vertical(id="fields-step-2"):
                    yield Label("Database engine", classes="field-label")
                    with RadioSet(id="radio-engine"):
                        yield RadioButton("SQLite  (simple, file-based, great for dev)", value=True)
                        yield RadioButton("PostgreSQL  (production-grade, full-featured)")
                # ── step 3: db connection ──
                with Vertical(id="fields-step-3"):
                    yield Label("Database name / file path", classes="field-label")
                    yield Input(placeholder="db.sqlite3  or  mydb", id="inp-db-name")
                    yield Label("Host  (PostgreSQL only)", classes="field-label")
                    yield Input(placeholder="localhost", id="inp-db-host")
                    yield Label("Port  (PostgreSQL only)", classes="field-label")
                    yield Input(placeholder="5432", id="inp-db-port")
                    yield Label("User  (PostgreSQL only)", classes="field-label")
                    yield Input(placeholder="postgres", id="inp-db-user")
                    yield Label("Password  (PostgreSQL only)", classes="field-label")
                    yield Input(placeholder="••••••••", password=True, id="inp-db-pass")
                # ── step 4: first model ──
                with Vertical(id="fields-step-4"):
                    yield Label("Model class name", classes="field-label")
                    yield Input(placeholder="e.g. User", id="inp-model-name")
                    yield Label(
                        "Fields  (comma-separated, e.g. name:CharField,email:EmailField)",
                        classes="field-label",
                    )
                    yield Input(
                        placeholder="name:CharField,email:EmailField,created_at:DateTimeField",
                        id="inp-model-fields",
                    )
                # ── step 5: author info ──
                with Vertical(id="fields-step-5"):
                    yield Label("Author name", classes="field-label")
                    yield Input(placeholder="Grace Peter Mutiibwa", id="inp-author")
                    yield Label("Author email", classes="field-label")
                    yield Input(placeholder="grace@example.com", id="inp-author-email")
                    yield Label("Version", classes="field-label")
                    yield Input(placeholder="0.1.0", id="inp-version")
                # ── buttons ──
                with Horizontal(id="btn-row"):
                    yield Button("← Back", id="btn-back", classes="secondary")
                    yield Button("Next →", id="btn-next", classes="primary")
        yield Footer()

    def on_mount(self):
        self._render_step()

    # ── step rendering ────────────────────────────────────────────────────────

    def _render_step(self):
        s = self.step
        title, subtitle, _ = self.STEPS[s - 1]

        self.query_one("#step-title").update(title)
        self.query_one("#step-subtitle").update(f"[dim]{subtitle}[/dim]")

        # Progress dots
        dots = ""
        for i in range(1, TOTAL_STEPS + 1):
            if i < s:
                dots += f"[bold green]●[/] "
            elif i == s:
                dots += f"[bold red]◉[/] "
            else:
                dots += "[dim]○[/] "
        self.query_one("#progress-row").update(dots.strip())

        # Show/hide field panels
        for n in range(1, TOTAL_STEPS + 1):
            panel = self.query_one(f"#fields-step-{n}")
            panel.display = n == s

        # Update db-details defaults based on engine selection
        if s == 3:
            engine = self._get_engine()
            is_pg = engine == "postgresql"
            self.query_one("#inp-db-host").display = is_pg
            self.query_one("#inp-db-port").display = is_pg
            self.query_one("#inp-db-user").display = is_pg
            self.query_one("#inp-db-pass").display = is_pg
            placeholder = "mydb" if is_pg else "db.sqlite3"
            self.query_one("#inp-db-name", Input).placeholder = placeholder

        # Back button visibility
        self.query_one("#btn-back").display = s > 1

        # Next/Finish label
        btn = self.query_one("#btn-next", Button)
        btn.label = "Finish ✓" if s == TOTAL_STEPS else "Next →"

    # ── helpers ───────────────────────────────────────────────────────────────

    def _get_engine(self) -> str:
        radio = self.query_one("#radio-engine", RadioSet)
        idx = radio.pressed_index
        return "postgresql" if idx == 1 else "sqlite"

    def _collect_step(self):
        s = self.step
        if s == 1:
            name = self.query_one("#inp-project-name", Input).value.strip() or "my_project"
            pkg = self.query_one("#inp-package-name", Input).value.strip() or name.lower().replace(
                "-", "_"
            ).replace(" ", "_")
            self._answers["project_name"] = name
            self._answers["package_name"] = pkg
        elif s == 2:
            self._answers["db_engine"] = self._get_engine()
        elif s == 3:
            self._answers["db_name"] = self.query_one("#inp-db-name", Input).value.strip()
            self._answers["db_host"] = (
                self.query_one("#inp-db-host", Input).value.strip() or "localhost"
            )
            self._answers["db_port"] = self.query_one("#inp-db-port", Input).value.strip() or "5432"
            self._answers["db_user"] = (
                self.query_one("#inp-db-user", Input).value.strip() or "postgres"
            )
            self._answers["db_password"] = self.query_one("#inp-db-pass", Input).value.strip()
        elif s == 4:
            self._answers["model_name"] = self.query_one("#inp-model-name", Input).value.strip()
            self._answers["model_fields"] = self.query_one("#inp-model-fields", Input).value.strip()
        elif s == 5:
            self._answers["author"] = (
                self.query_one("#inp-author", Input).value.strip() or "Grace Peter Mutiibwa"
            )
            self._answers["author_email"] = self.query_one("#inp-author-email", Input).value.strip()
            self._answers["version"] = (
                self.query_one("#inp-version", Input).value.strip() or "0.1.0"
            )

    # ── auto-fill package name from project name ──────────────────────────────

    @on(Input.Changed, "#inp-project-name")
    def _sync_package_name(self, event: Input.Changed):
        auto = event.value.lower().replace("-", "_").replace(" ", "_")
        pkg_inp = self.query_one("#inp-package-name", Input)
        if not pkg_inp.value or pkg_inp.value == getattr(self, "_last_auto", ""):
            pkg_inp.value = auto
            self._last_auto = auto

    # ── navigation ────────────────────────────────────────────────────────────

    @on(Button.Pressed, "#btn-next")
    def _next(self):
        self._collect_step()
        if self.step == TOTAL_STEPS:
            scaffold(self._answers)
            self.app.push_screen(DoneScreen(self._answers))
        else:
            self.step += 1
            self._render_step()

    @on(Button.Pressed, "#btn-back")
    def _back(self):
        if self.step > 1:
            self.step -= 1
            self._render_step()

    def action_back(self):
        if self.step > 1:
            self.step -= 1
            self._render_step()
        else:
            self.app.pop_screen()


# ── Done screen ───────────────────────────────────────────────────────────────


class DoneScreen(Screen):
    BINDINGS = [Binding("q", "quit_app", "Quit"), Binding("enter", "quit_app", "Quit")]

    def __init__(self, answers: dict):
        super().__init__()
        self._answers = answers

    def compose(self) -> ComposeResult:
        a = self._answers
        proj = a.get("project_name", "my_project")
        pkg = a.get("package_name", proj)
        eng = a.get("db_engine", "sqlite")
        db = a.get("db_name", "db.sqlite3")
        model = a.get("model_name", "")

        lines = [
            f"[bold green]✓[/] Project folder:  [bold]{proj}/[/]",
            f"[bold green]✓[/] Package:         [bold]{pkg}/[/]",
            f"[bold green]✓[/] Settings:        [bold]settings.py[/]",
            f"[bold green]✓[/] Database:        [bold]{eng}[/]  →  [dim]{db}[/dim]",
            f"[bold green]✓[/] Migrations dir:  [bold]migrations/[/]",
        ]
        if model:
            lines.append(f"[bold green]✓[/] First model:     [bold]{model}[/]  →  {pkg}/models.py")
        lines += [
            "",
            "[bold yellow]Next steps:[/]",
            f"  [cyan]cd {proj}[/]",
            "  [cyan]pookiedb migrate --settings settings.py[/]",
            "  [cyan]pookiedb shell   --settings settings.py[/]",
        ]

        yield Header(show_clock=False)
        with Center():
            with Vertical(id="summary-box"):
                yield Static("🐾  Project scaffolded successfully!", id="summary-title")
                yield Static("\n".join(lines), id="summary-body")
                yield Static("Press [bold green]Enter[/] or [bold red]Q[/] to exit", id="done-hint")
        yield Footer()

    def action_quit_app(self):
        self.app.exit(result=self._answers)


# ── Main app ──────────────────────────────────────────────────────────────────


class PookieInitApp(App):
    CSS = POOKIE_CSS
    TITLE = "Pookie ORM: Project Wizard"

    def on_mount(self):
        self.push_screen(SplashScreen())


# ── Scaffolding logic ─────────────────────────────────────────────────────────

_FIELD_DEFAULTS = {
    "CharField": "pookiedb.CharField(max_length=255)",
    "TextField": "pookiedb.TextField()",
    "IntegerField": "pookiedb.IntegerField()",
    "BooleanField": "pookiedb.BooleanField(default=False)",
    "DateTimeField": "pookiedb.DateTimeField(auto_now_add=True)",
    "DateField": "pookiedb.DateField()",
    "EmailField": "pookiedb.EmailField()",
    "FloatField": "pookiedb.FloatField()",
    "UUIDField": "pookiedb.UUIDField(auto=True)",
    "JSONField": "pookiedb.JSONField(null=True)",
    "SlugField": "pookiedb.SlugField()",
}


def _parse_fields(raw: str) -> list[tuple[str, str]]:
    """Parse 'name:CharField,email:EmailField' → [('name', 'CharField'), ...]"""
    result = []
    if not raw.strip():
        return result
    for part in raw.split(","):
        part = part.strip()
        if ":" in part:
            fname, ftype = part.split(":", 1)
        else:
            fname, ftype = part, "CharField"
        fname = fname.strip().lower().replace(" ", "_")
        ftype = ftype.strip()
        if not ftype[0].isupper():
            ftype = ftype.capitalize() + "Field"
        result.append((fname, ftype))
    return result


def scaffold(answers: dict):
    """Write the project skeleton to disk."""
    proj = answers.get("project_name", "my_project")
    pkg = answers.get("package_name", proj.lower().replace("-", "_").replace(" ", "_"))
    engine = answers.get("db_engine", "sqlite")
    db_name = answers.get("db_name") or ("db.sqlite3" if engine == "sqlite" else "mydb")
    db_host = answers.get("db_host", "localhost")
    db_port = answers.get("db_port", "5432")
    db_user = answers.get("db_user", "postgres")
    db_pass = answers.get("db_password", "")
    model = answers.get("model_name", "").strip()
    mfields = answers.get("model_fields", "").strip()
    author = answers.get("author", "Grace Peter Mutiibwa")
    a_email = answers.get("author_email", "")
    version = answers.get("version", "0.1.0")

    root = Path(proj)
    (root / pkg).mkdir(parents=True, exist_ok=True)
    (root / "migrations").mkdir(exist_ok=True)

    # ── settings.py ──────────────────────────────────────────────────────────
    if engine == "sqlite":
        connect_call = f'pookie.connect("sqlite:///{db_name}")'
    else:
        connect_call = (
            f"pookie.connect(\n"
            f'    engine="postgresql",\n'
            f'    name="{db_name}",\n'
            f'    host="{db_host}",\n'
            f"    port={db_port},\n"
            f'    user="{db_user}",\n'
            f'    password="{db_pass}",\n'
            f")"
        )
    model_import = (
        f"from {pkg}.models import *  # noqa" if model else f"# from {pkg}.models import YourModel"
    )

    settings_src = textwrap.dedent(
        f"""\
        \"\"\"
        {proj}: Pookie ORM settings
        Author: {author}
        \"\"\"
        import pookiedb

        # ── Database ──────────────────────────────────────────────────────────
        {connect_call}

        # ── Import your models so they are registered ─────────────────────────
        {model_import}
    """
    )
    (root / "settings.py").write_text(settings_src)

    # ── pkg/__init__.py ───────────────────────────────────────────────────────
    (root / pkg / "__init__.py").write_text(f"# {pkg}\n")

    # ── pkg/models.py ─────────────────────────────────────────────────────────
    if model:
        table_name = model.lower() + "s"
        parsed = _parse_fields(mfields)
        if not parsed:
            parsed = [("name", "CharField")]
        field_lines = []
        for fname, ftype in parsed:
            field_expr = _FIELD_DEFAULTS.get(ftype, f"pookie.CharField(max_length=255)  # {ftype}")
            field_lines.append(f"    {fname} = {field_expr}")
        fields_str = "\n".join(field_lines)
        models_src = (
            f'"""{chr(10)}{proj} models{chr(10)}Author: {author}{chr(10)}"""\n'
            "import pookiedb\n\n\n"
            f"class {model}(pookiedb.Model):\n"
            f"{fields_str}\n\n"
            "    class Meta:\n"
            f'        db_table = "{table_name}"\n'
            '        ordering = ["-id"]\n'
        )
    else:
        models_src = (
            f'"""{chr(10)}{proj} models{chr(10)}Author: {author}{chr(10)}"""\n'
            "import pookiedb\n\n\n"
            "# Define your models here. Example:\n"
            "#\n"
            "# class Post(pookiedb.Model):\n"
            "#     title = pookiedb.CharField(max_length=200)\n"
            "#     body  = pookie.TextField()\n"
            "#\n"
            "#     class Meta:\n"
            '#         db_table = "posts"\n'
        )
    (root / pkg / "models.py").write_text(models_src)

    # ── pyproject.toml ────────────────────────────────────────────────────────
    author_entry = f'{{name = "{author}"' + (f', email = "{a_email}"' if a_email else "") + "}"
    pyproject_src = textwrap.dedent(
        f"""\
        [build-system]
        requires = ["setuptools>=68", "wheel"]
        build-backend = "setuptools.build_meta"

        [project]
        name = "{pkg}"
        version = "{version}"
        description = ""
        authors = [{author_entry}]
        requires-python = ">=3.10"
        dependencies = ["pookiedb"]

        [tool.setuptools.packages.find]
        where = ["."]
        include = ["{pkg}*"]
    """
    )
    (root / "pyproject.toml").write_text(pyproject_src)

    # ── migrations/__init__.py ────────────────────────────────────────────────
    (root / "migrations" / "__init__.py").write_text("")

    # ── README.md ─────────────────────────────────────────────────────────────
    readme_src = textwrap.dedent(
        f"""\
        # {proj}

        A [Pookie ORM](https://github.com/gracepeter/pookie) project.

        **Author:** {author}
        **Version:** {version}

        ## Setup

        ```bash
        pip install pookie{' psycopg2-binary' if engine == 'postgresql' else ''}
        ```

        ## Migrations

        ```bash
        pookiedb makemigrations --settings settings.py
        pookiedb migrate        --settings settings.py
        ```

        ## Shell

        ```bash
        pookiedb shell --settings settings.py
        ```
    """
    )
    (root / "README.md").write_text(readme_src)

    # ── .gitignore ────────────────────────────────────────────────────────────
    gitignore = textwrap.dedent(
        """\
        __pycache__/
        *.py[cod]
        *.sqlite3
        .env
        dist/
        build/
        *.egg-info/
        migrations/.pookie_snapshot.json
    """
    )
    (root / ".gitignore").write_text(gitignore)


# ── Entry point (called from CLI) ─────────────────────────────────────────────


def run_init():
    """Launch the Textual wizard and return the answers dict."""
    app = PookieInitApp()
    return app.run()
