import os
import sys
import importlib

import click
from rich.console import Console
from rich.table import Table
from rich import print as rprint
from rich.panel import Panel
from rich.text import Text

console = Console()


def _banner():
    """Print the Pookie ASCII art banner."""
    art = Text()
    art.append("  ██████╗  ██████╗  ██████╗ ██╗  ██╗██╗███████╗\n", style="bold red")
    art.append("  ██╔══██╗██╔═══██╗██╔═══██╗██║ ██╔╝██║██╔════╝\n", style="bold red")
    art.append("  ██████╔╝██║   ██║██║   ██║█████╔╝ ██║█████╗  \n", style="bold red")
    art.append("  ██╔═══╝ ██║   ██║██║   ██║██╔═██╗ ██║██╔══╝  \n", style="bold red")
    art.append("  ██║     ╚██████╔╝╚██████╔╝██║  ██╗██║███████╗\n", style="bold red")
    art.append("  ╚═╝      ╚═════╝  ╚═════╝ ╚═╝  ╚═╝╚═╝╚══════╝\n", style="bold red")
    art.append("  🐾  A Django-style ORM for PostgreSQL & SQLite", style="italic cyan")
    console.print(Panel(art, border_style="red", padding=(0, 1)))


def _load_settings(settings: str):
    """Import user settings module to register models and call pookie.connect()."""
    if not settings:
        return
    spec = importlib.util.spec_from_file_location("pookie_settings", settings)
    if spec is None:
        # Try as a dotted module name
        try:
            importlib.import_module(settings)
        except ImportError:
            console.print(f"[yellow]Warning: Could not import settings module '{settings}'[/yellow]")
        return
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pookie_settings"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        console.print(f"[red]Error loading settings '{settings}': {e}[/red]")


@click.group(invoke_without_command=True)
@click.version_option("0.1.0", prog_name="pookiedb")
@click.pass_context
def cli(ctx):
    """🐾 Pookie ORM — A Django-style ORM by Grace Peter Mutiibwa."""
    if ctx.invoked_subcommand is None:
        _banner()
        console.print("\nRun [bold cyan]pookiedb --help[/bold cyan] for available commands.\n")


@cli.command("init")
def init():
    """Interactively scaffold a new Pookie project (Textual TUI wizard)."""
    try:
        from pookiedb.cli.tui import run_init
        run_init()
    except ImportError:
        console.print("[red]✗ Textual is required for pookiedb init.[/red]")
        console.print("  Install it with: [bold]pip install textual[/bold]")
        raise SystemExit(1)


# ── makemigrations ────────────────────────────────────────────────────────────

@cli.command("makemigrations")
@click.option("--name", "-n", default=None, help="Custom name for the migration.")
@click.option("--migrations-dir", "-d", default="migrations", show_default=True,
              help="Directory to write migration files into.")
@click.option("--db", default="default", show_default=True, help="DB alias to target.")
@click.option("--settings", "-s", default=None, help="Path or dotted module to your settings file.")
def makemigrations(name, migrations_dir, db, settings):
    """Detect model changes and generate a new migration file."""
    _load_settings(settings)
    from pookiedb.migrations.runner import make_migrations

    _banner()
    console.print("[bold cyan]  makemigrations[/bold cyan]\n")
    try:
        path = make_migrations(migrations_dir, name=name, db_alias=db)
        if path:
            console.print(f"[green]✓ Migration created:[/green] {path}")
        else:
            console.print("[dim]No changes detected — nothing to migrate.[/dim]")
    except Exception as e:
        console.print(f"[red]✗ Error:[/red] {e}")
        raise SystemExit(1)


# ── migrate ───────────────────────────────────────────────────────────────────

@cli.command("migrate")
@click.option("--migrations-dir", "-d", default="migrations", show_default=True,
              help="Directory containing migration files.")
@click.option("--db", default="default", show_default=True, help="DB alias to target.")
@click.option("--fake", is_flag=True, default=False,
              help="Mark migrations as applied without running SQL.")
@click.option("--settings", "-s", default=None, help="Path or dotted module to your settings file.")
def migrate(migrations_dir, db, fake, settings):
    """Apply all pending migrations to the database."""
    _load_settings(settings)
    from pookiedb.migrations.executor import migrate as run_migrate

    _banner()
    console.print("[bold cyan]  migrate[/bold cyan]\n")
    try:
        applied = run_migrate(migrations_dir, db_alias=db, fake=fake)
        if applied:
            for m in applied:
                tag = "[dim](fake)[/dim] " if fake else ""
                console.print(f"  [green]✓[/green] {tag}Applied: [bold]{m}[/bold]")
            console.print(f"\n[green]Done.[/green] {len(applied)} migration(s) applied.")
        else:
            console.print("[dim]Already up to date. No migrations to apply.[/dim]")
    except Exception as e:
        console.print(f"[red]✗ Migration failed:[/red] {e}")
        raise SystemExit(1)


# ── rollback ──────────────────────────────────────────────────────────────────

@cli.command("rollback")
@click.option("--steps", default=1, show_default=True, help="Number of migrations to roll back.")
@click.option("--migrations-dir", "-d", default="migrations", show_default=True)
@click.option("--db", default="default", show_default=True)
@click.option("--settings", "-s", default=None)
def rollback(steps, migrations_dir, db, settings):
    """Roll back the last N applied migrations."""
    _load_settings(settings)
    from pookiedb.migrations.executor import rollback as run_rollback

    _banner()
    console.print(f"[bold cyan]  rollback ({steps} step(s))[/bold cyan]\n")
    try:
        rolled = run_rollback(migrations_dir, db_alias=db, steps=steps)
        if rolled:
            for m in rolled:
                console.print(f"  [yellow]↩[/yellow] Rolled back: [bold]{m}[/bold]")
        else:
            console.print("[dim]Nothing to roll back.[/dim]")
    except Exception as e:
        console.print(f"[red]✗ Rollback failed:[/red] {e}")
        raise SystemExit(1)


# ── showmigrations ────────────────────────────────────────────────────────────

@cli.command("showmigrations")
@click.option("--migrations-dir", "-d", default="migrations", show_default=True)
@click.option("--db", default="default", show_default=True)
@click.option("--settings", "-s", default=None)
def showmigrations(migrations_dir, db, settings):
    """List all migrations and their applied status."""
    _load_settings(settings)
    from pookiedb.migrations.executor import show_migrations

    _banner()
    console.print("[bold cyan]  showmigrations[/bold cyan]\n")
    try:
        migs = show_migrations(migrations_dir, db_alias=db)
        if not migs:
            console.print("[dim]No migration files found.[/dim]")
            return
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Status", width=8)
        table.add_column("Migration Name")
        for m in migs:
            status = "[green]✓[/green]" if m["applied"] else "[dim]○[/dim]"
            table.add_row(status, m["name"])
        console.print(table)
    except Exception as e:
        console.print(f"[red]✗ Error:[/red] {e}")
        raise SystemExit(1)


# ── shell ─────────────────────────────────────────────────────────────────────

@cli.command("shell")
@click.option("--settings", "-s", default=None, help="Settings file to load before shell starts.")
def shell(settings):
    """
    Start an interactive Python shell with pookie pre-imported.

    All registered models and pookie itself are available in the REPL.
    """
    _load_settings(settings)

    import pookiedb
    from pookiedb.db.registry import registry

    _banner()
    console.print(
        f"\n[bold cyan]  Pookie Shell[/bold cyan]  (Python {sys.version.split()[0]})\n"
        "  [dim]`pookie` and all registered models are available.[/dim]\n"
        "  [dim]Type [bold]exit()[/bold] or Ctrl-D to quit.[/dim]\n"
    )

    local_vars = {"pookiedb": pookie}
    for model in registry.all():
        local_vars[model.__name__] = model

    try:
        import readline  # noqa: F401 — enables history in some terminals
    except ImportError:
        pass

    try:
        from IPython import start_ipython
        start_ipython(argv=[], user_ns=local_vars)
    except ImportError:
        import code
        code.interact(local=local_vars, banner="")


# ── dbshell ───────────────────────────────────────────────────────────────────

@cli.command("dbshell")
@click.option("--db", default="default", show_default=True)
@click.option("--settings", "-s", default=None)
def dbshell(db, settings):
    """Open a raw database shell (psql for PostgreSQL, sqlite3 for SQLite)."""
    _load_settings(settings)
    from pookiedb.db.connection import get_connection

    try:
        pool = get_connection(db)
    except Exception as e:
        console.print(f"[red]✗ Could not get connection:[/red] {e}")
        raise SystemExit(1)

    cfg = pool.config
    _banner()
    console.print(f"[bold cyan]  dbshell[/bold cyan]  ({cfg.engine})\n")

    if cfg.is_sqlite:
        import subprocess
        db_name = cfg.name if cfg.name != ":memory:" else ""
        if not db_name:
            console.print("[red]Cannot open dbshell for an in-memory SQLite database.[/red]")
            raise SystemExit(1)
        result = subprocess.run(["sqlite3", db_name])
        sys.exit(result.returncode)
    else:
        import subprocess
        env = os.environ.copy()
        if cfg.password:
            env["PGPASSWORD"] = cfg.password
        cmd = [
            "psql",
            "-h", cfg.host,
            "-p", str(cfg.port),
            "-U", cfg.user,
            cfg.name,
        ]
        result = subprocess.run(cmd, env=env)
        sys.exit(result.returncode)


# ── inspectdb ─────────────────────────────────────────────────────────────────

@cli.command("inspectdb")
@click.option("--db", default="default", show_default=True)
@click.option("--settings", "-s", default=None)
@click.option("--table", "-t", default=None, help="Inspect a single table only.")
@click.option("--output", "-o", default=None, help="Write output to a file instead of stdout.")
def inspectdb(db, settings, table, output):
    """
    Introspect an existing database and generate Pookie model code.

    Reads the live database schema and outputs Python model classes
    you can paste directly into your project.
    """
    _load_settings(settings)
    from pookiedb.db.connection import get_connection, execute

    try:
        pool = get_connection(db)
    except Exception as e:
        console.print(f"[red]✗ Could not get connection:[/red] {e}")
        raise SystemExit(1)

    cfg = pool.config
    _banner()
    console.print(f"[bold cyan]  inspectdb[/bold cyan]  ({cfg.engine})\n")

    lines = [
        "# Auto-generated by pookiedb inspectdb",
        "# Author: Grace Peter Mutiibwa",
        "# Review and adjust before use — field types may need tweaking.",
        "",
        "import pookiedb",
        "",
        "",
    ]

    if cfg.is_sqlite:
        tables = _inspect_sqlite(pool, table)
    else:
        tables = _inspect_postgres(pool, table)

    for tbl_name, columns in tables.items():
        class_name = _to_class_name(tbl_name)
        lines.append(f"class {class_name}(pookie.Model):")
        for col in columns:
            lines.append(f"    {col}")
        lines.append("")
        lines.append(f"    class Meta:")
        lines.append(f"        db_table = {tbl_name!r}")
        lines.append("")
        lines.append("")

    output_text = "\n".join(lines)

    if output:
        with open(output, "w") as f:
            f.write(output_text)
        console.print(f"[green]✓ Written to {output}[/green]")
    else:
        console.print(output_text)


def _to_class_name(table: str) -> str:
    return "".join(w.capitalize() for w in table.split("_"))


def _pg_type_to_field(pg_type: str, char_max: int = None) -> str:
    pg_type = pg_type.lower()
    if "char" in pg_type or "varchar" in pg_type:
        length = char_max or 255
        return f"pookie.CharField(max_length={length})"
    if pg_type in ("text", "citext"):
        return "pookie.TextField()"
    if pg_type in ("integer", "int4", "int"):
        return "pookie.IntegerField()"
    if pg_type in ("bigint", "int8"):
        return "pookie.BigIntegerField()"
    if pg_type in ("real", "float4"):
        return "pookie.FloatField()"
    if pg_type in ("double precision", "float8"):
        return "pookie.FloatField()"
    if "numeric" in pg_type or "decimal" in pg_type:
        return "pookie.DecimalField()"
    if pg_type == "boolean":
        return "pookie.BooleanField()"
    if pg_type == "date":
        return "pookie.DateField()"
    if "timestamp" in pg_type:
        return "pookie.DateTimeField()"
    if pg_type == "time":
        return "pookie.TimeField()"
    if pg_type in ("json", "jsonb"):
        return "pookie.JSONField()"
    if pg_type == "uuid":
        return "pookie.UUIDField()"
    return f"pookie.TextField()  # unmapped type: {pg_type}"


def _inspect_postgres(pool, target_table):
    from pookiedb.db.connection import execute
    alias = "default"

    table_query = """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """
    rows = execute(table_query, alias=alias, fetch="all") or []
    tables = {}

    for row in rows:
        tbl = row["table_name"]
        if target_table and tbl != target_table:
            continue
        if tbl == "pookie_migrations":
            continue

        col_query = """
            SELECT column_name, data_type, character_maximum_length,
                   is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
        """
        cols = execute(col_query, [tbl], alias=alias, fetch="all") or []
        fields = []
        for col in cols:
            cname = col["column_name"]
            field_str = _pg_type_to_field(col["data_type"], col.get("character_maximum_length"))
            null_kw = ", null=True, blank=True" if col["is_nullable"] == "YES" else ""
            if field_str.endswith(")") and null_kw:
                inner = field_str[field_str.index("(")+1:-1].strip()
                sep = ", " if inner else ""
                field_str = field_str[:field_str.index("(")+1] + inner + sep + null_kw.lstrip(", ") + ")"
            fields.append(f"{cname} = {field_str}")
        tables[tbl] = fields

    return tables


def _sqlite_type_to_field(sqlite_type: str) -> str:
    t = sqlite_type.upper()
    if "CHAR" in t or "TEXT" in t or "CLOB" in t:
        if "VARCHAR" in t:
            import re
            m = re.search(r"\((\d+)\)", t)
            length = m.group(1) if m else "255"
            return f"pookie.CharField(max_length={length})"
        return "pookie.TextField()"
    if "INT" in t:
        return "pookie.IntegerField()"
    if "REAL" in t or "FLOA" in t or "DOUB" in t:
        return "pookie.FloatField()"
    if "BLOB" in t or not t:
        return "pookie.TextField()  # BLOB"
    if "BOOL" in t:
        return "pookie.BooleanField()"
    if "DATE" in t:
        return "pookie.DateField()"
    if "TIME" in t:
        return "pookie.DateTimeField()"
    return f"pookie.TextField()  # unmapped: {sqlite_type}"


def _inspect_sqlite(pool, target_table):
    from pookiedb.db.connection import execute
    alias = "default"

    rows = execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
        alias=alias, fetch="all"
    ) or []

    tables = {}
    for row in rows:
        tbl = row["name"]
        if target_table and tbl != target_table:
            continue
        if tbl in ("pookie_migrations", "sqlite_sequence"):
            continue

        cols = execute(f'PRAGMA table_info("{tbl}")', alias=alias, fetch="all") or []
        fields = []
        for col in cols:
            cname = col["name"]
            field_str = _sqlite_type_to_field(col["type"])
            nullable = col["notnull"] == 0
            if nullable and not col["pk"]:
                # Insert null/blank before closing paren, with comma only if there are existing args
                inner = field_str[field_str.index("(")+1:-1].strip()
                sep = ", " if inner else ""
                field_str = field_str[:field_str.index("(")+1] + inner + sep + "null=True, blank=True)"
            fields.append(f"{cname} = {field_str}")
        tables[tbl] = fields

    return tables
