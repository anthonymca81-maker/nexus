r"""
NEXUS TECH STORE
Desktop e-commerce app (customer store + admin dashboard) on Microsoft SQL Server.

Install:   pip install customtkinter pyodbc
           (+ the free "ODBC Driver 18 for SQL Server" from Microsoft, if not installed yet)
Run:       python nexus_tech_store.py

Database (required): Microsoft SQL Server (Express / Developer / LocalDB), managed with SSMS.
  1. In SSMS, open nexus_tech_store.sql and Execute it once (it creates the database,
     all tables, the Monitors & Accessories tables, the views and the demo data).
  2. Set DB_SERVER below (section "DATABASE LAYER") to the server name SSMS connects to,
     e.g. localhost\SQLEXPRESS. Windows Authentication is used unless DB_USER is set.
  This single file contains the whole database connection - there is no separate db.py.

Demo admin login:  username: admin   password: admin123
Demo customer:     username: customer  password: customer123
Any other username you register with becomes a new customer account.

No mock/in-memory data: products, categories, users, cart checkout, and sales
analytics all read from and write to the SQL Server database.

PRODUCT IMAGES
  Put image files in an "images" folder next to this script. Name each file after
  the product (lowercase, dashes), e.g. images/sony-wh-1000xm6.png. See images/README.txt
  for the exact filename of every product. Missing images fall back to the emoji icon.
  Admins can also attach an image when adding/editing a product.
"""

import hashlib
import os
import re
import shutil
import sys
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image  # installed automatically with customtkinter
from dotenv import load_dotenv

load_dotenv()  # loads .env from the project root into os.environ

try:
    import pyodbc
except ImportError:  # show a readable message instead of a console traceback
    import tkinter
    _root = tkinter.Tk()
    _root.withdraw()
    messagebox.showerror(
        "Missing package",
        "The 'pyodbc' package is not installed.\n\nOpen a terminal and run:\n    pip install pyodbc")
    sys.exit(1)


# ============================================================
# CONSTANTS
# ============================================================
DARK_BG, DARK_PANEL, DARK_CARD, DARK_BORDER = "#080b10", "#10151d", "#151c26", "#263242"
LIGHT_BG, LIGHT_PANEL, LIGHT_CARD, LIGHT_BORDER = "#f3f6fa", "#ffffff", "#ffffff", "#d8e0ea"
NEON, NEON_BLUE = "#67ffb1", "#4da3ff"

GREEN = ("#1e7d50", "#18b96c")
GREEN_HOVER = ("#176741", "#12d879")
GREEN_TEXT = ("white", "#06140d")
BAR_BLUE = ("#3b82f6", "#4da3ff")
MUTED = ("gray45", "#8fa0b4")

ADMIN_USER, ADMIN_PASS = "admin", "admin123"

IMAGE_DIR = Path(__file__).resolve().parent / "images"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")



# ============================================================
# DATABASE LAYER  (Microsoft SQL Server via pyodbc)
# The ONE and only place the app connects to the database.
# ============================================================
# Connection settings. Use the server name that SSMS shows in its "Connect to Server" box.
# Common values:  localhost\SQLEXPRESS   .\SQLEXPRESS   (localdb)\MSSQLLocalDB   localhost
# Each value can also be overridden with an environment variable of the same name.
DB_SERVER = os.environ.get("NEXUS_DB_SERVER", r"localhost\SQLEXPRESS")
DB_NAME = os.environ.get("NEXUS_DB_NAME", "nexus_tech_store")
DB_USER = os.environ.get("NEXUS_DB_USER", "")          # leave empty = Windows Authentication
DB_PASSWORD = os.environ.get("NEXUS_DB_PASSWORD", "")  # only used together with DB_USER
DB_DRIVER = os.environ.get("NEXUS_DB_DRIVER", "")      # empty = newest installed SQL Server ODBC driver

DBError = pyodbc.Error  # every database failure the app has to handle


class DBSetupError(pyodbc.Error):
    """Connection is possible but the setup is wrong (no driver, tables missing, ...)."""


def friendly_error(err):
    """Readable text for a database error (pyodbc packs it as a (sqlstate, message) tuple)."""
    args = getattr(err, "args", ())
    if isinstance(err, pyodbc.Error) and len(args) >= 2 and isinstance(args[1], str):
        return args[1]
    return str(err)


class _CartError(Exception):
    """Internal: a checkout rule failed (empty stock, invalid item). The transaction is rolled back."""


# ---------------- Monitors & Accessories ----------------
# Categories whose products have an extra row in monitor_specs / monitor_accessory_specs.
MONITOR_CATEGORIES = {"Monitors"}
ACCESSORY_CATEGORIES = {"Monitor Arms", "Display Cables", "Webcams & Lights"}

SPEC_TABLES = {"monitor": "monitor_specs", "accessory": "monitor_accessory_specs"}

PANEL_TYPES = ["IPS", "VA", "TN", "OLED", "QD-OLED", "Mini-LED", "Other"]
ACCESSORY_TYPES = ["arm", "stand", "cable", "adapter", "webcam", "light_bar", "other"]

# (column, label, type, required, choices-or-default)
#   type: text | int | float | bool | choice | resolution | ratio
SPEC_FIELDS = {
    "monitor": [
        ("screen_size_in",   "Screen size (inches)",          "float",      True,  ""),
        ("resolution",       "Resolution (e.g. 2560x1440)",   "resolution", True,  ""),
        ("panel_type",       "Panel type",                    "choice",     True,  PANEL_TYPES),
        ("refresh_rate_hz",  "Refresh rate (Hz)",             "int",        True,  "60"),
        ("response_time_ms", "Response time (ms)",            "float",      False, ""),
        ("aspect_ratio",     "Aspect ratio (e.g. 16:9)",      "ratio",      True,  "16:9"),
        ("is_curved",        "Curved screen",                 "bool",       False, ""),
        ("hdr_support",      "HDR support (optional)",        "text",       False, ""),
        ("ports",            "Ports (optional)",              "text",       False, ""),
    ],
    "accessory": [
        ("accessory_type",   "Accessory type",                "choice",     True,  ACCESSORY_TYPES),
        ("compatibility",    "Compatibility (optional)",      "text",       False, ""),
        ("connector_type",   "Connector (optional)",          "text",       False, ""),
        ("length_m",         "Cable length in metres (optional)", "float",  False, ""),
        ("max_load_kg",      "Max load in kg (optional)",     "float",      False, ""),
        ("vesa_pattern",     "VESA pattern (optional)",       "text",       False, ""),
    ],
}

# Tables / views (and the columns) the app cannot run without. Checked once at start-up so a
# wrong or outdated database gives ONE clear message instead of errors in random screens.
REQUIRED_SCHEMA = {
    "users": ["id", "username", "full_name", "email", "password_hash", "role"],
    "departments": ["id", "name"],
    "categories": ["id", "department_id", "name"],
    "products": ["id", "category_id", "name", "specs", "price", "rating", "icon",
                 "image_path", "stock", "is_active", "updated_at"],
    "orders": ["id", "user_id", "total", "status", "created_at"],
    "order_items": ["id", "order_id", "product_id", "product_name", "category_name",
                    "quantity", "unit_price"],
    "v_catalog": ["id", "name", "department", "category", "category_id", "specs", "price",
                  "rating", "icon", "image_path", "stock"],
    "v_sales": ["product_name", "category_name", "quantity", "unit_price", "sold_at"],
}

_DRIVER_PREFERENCE = (
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "ODBC Driver 13.1 for SQL Server",
    "ODBC Driver 13 for SQL Server",
    "ODBC Driver 11 for SQL Server",
    "SQL Server Native Client 11.0",
    "SQL Server Native Client 10.0",
    "SQL Server",
)


def spec_kind(category):
    """'monitor', 'accessory' or None, depending on the category name."""
    if category in MONITOR_CATEGORIES:
        return "monitor"
    if category in ACCESSORY_CATEGORIES:
        return "accessory"
    return None


def build_specs_summary(kind, data):
    """One-line summary for products.specs, built from the detail fields."""
    d = data or {}
    if kind == "monitor":
        parts = [f'{d["screen_size_in"]:g}"', d["resolution"], d["panel_type"], f'{d["refresh_rate_hz"]}Hz']
        if d.get("response_time_ms"):
            parts.append(f'{d["response_time_ms"]:g}ms')
        if d.get("is_curved"):
            parts.append("Curved")
        if d.get("hdr_support"):
            parts.append(d["hdr_support"])
    elif kind == "accessory":
        parts = [d["accessory_type"].replace("_", " ").title()]
        if d.get("connector_type"):
            parts.append(d["connector_type"])
        if d.get("length_m"):
            parts.append(f'{d["length_m"]:g}m')
        if d.get("max_load_kg"):
            parts.append(f'Up to {d["max_load_kg"]:g}kg')
        if d.get("vesa_pattern"):
            parts.append(f'VESA {d["vesa_pattern"]}')
        if d.get("compatibility"):
            parts.append(d["compatibility"])
    else:
        return ""
    return " • ".join(str(x) for x in parts)[:255]


def hash_password(raw_password):
    """SHA-256 hex digest - matches the HASHBYTES('SHA2_256', ...) hashes of the seed users."""
    return hashlib.sha256(raw_password.encode("utf-8")).hexdigest()


class Database:
    """The app's single SQL Server connection (pyodbc).

    Raises pyodbc.Error (or DBSetupError, a subclass) if the connection or a query fails;
    the caller (the app) shows that to the user, since this class has no UI of its own.
    """

    def __init__(self, server=None, database=None, user=None, password=None, driver=None):
        self.server = server or DB_SERVER
        self.database = database or DB_NAME
        self.user = DB_USER if user is None else user
        self.password = DB_PASSWORD if password is None else password
        self.driver = driver or DB_DRIVER
        self.conn = None
        self.specs_available = False
        self.connect()

    # ---------------- connection ----------------
    def _pick_driver(self):
        installed = pyodbc.drivers()
        if self.driver:
            if self.driver not in installed:
                raise DBSetupError(
                    f"The ODBC driver '{self.driver}' is not installed. "
                    f"Installed drivers: {', '.join(installed) or 'none'}.")
            return self.driver
        for name in _DRIVER_PREFERENCE:
            if name in installed:
                return name
        raise DBSetupError(
            "No SQL Server ODBC driver was found. Install \"ODBC Driver 18 for SQL Server\" "
            "(free, from Microsoft) and start the app again.")

    def _connection_string(self):
        driver = self._pick_driver()
        parts = [f"DRIVER={{{driver}}}", f"SERVER={self.server}", f"DATABASE={self.database}",
                 "Connection Timeout=10"]
        if driver.startswith("ODBC Driver"):
            # Driver 18 encrypts by default; a local SQL Server has a self-signed certificate.
            parts.append("TrustServerCertificate=yes")
        if self.user:
            parts.append(f"UID={self.user}")
            parts.append("PWD={" + self.password.replace("}", "}}") + "}")
        else:
            parts.append("Trusted_Connection=yes")
        return ";".join(parts)

    def connect(self):
        self.close()
        # autocommit stays ON for single statements; multi-statement writes use _transaction().
        self.conn = pyodbc.connect(self._connection_string(), autocommit=True)
        try:
            self._verify_schema()
        except Exception:
            self.close()
            raise

    def _verify_schema(self):
        """Make sure every table/view/column the app needs exists in THIS database."""
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS")
            present = {}
            for table, column in cur.fetchall():
                present.setdefault(table.lower(), set()).add(column.lower())
        finally:
            cur.close()

        problems = []
        for table, columns in REQUIRED_SCHEMA.items():
            have = present.get(table)
            if have is None:
                problems.append(f"missing table/view: {table}")
            else:
                gone = [c for c in columns if c not in have]
                if gone:
                    problems.append(f"{table} is missing column(s): {', '.join(gone)}")
        if problems:
            raise DBSetupError(
                f"The database '{self.database}' does not match nexus_tech_store.sql:\n  - "
                + "\n  - ".join(problems)
                + "\nRun nexus_tech_store.sql in SSMS (it recreates the database).")

        # Monitors & Accessories are optional: the app works without them.
        self.specs_available = all(
            SPEC_TABLES[kind] in present
            and all(f[0] in present[SPEC_TABLES[kind]] for f in SPEC_FIELDS[kind])
            for kind in SPEC_TABLES)

    def _ensure_connected(self):
        if self.conn is None:
            self.connect()
            return
        try:
            cur = self.conn.cursor()
            try:
                cur.execute("SELECT 1").fetchone()
            finally:
                cur.close()
        except pyodbc.Error:
            self.connect()  # connection was dropped (server restarted, idle timeout...)

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except pyodbc.Error:
                pass
            self.conn = None

    # ---------------- query helpers ----------------
    @staticmethod
    def _as_dicts(cur):
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def _fetch(self, sql, params=()):
        """SELECT -> list of {column: value} dicts."""
        self._ensure_connected()
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            return self._as_dicts(cur)
        finally:
            cur.close()

    def _execute(self, sql, params=()):
        """Single INSERT/UPDATE/DELETE (autocommit)."""
        self._ensure_connected()
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
        finally:
            cur.close()

    @staticmethod
    def _new_id(cur):
        """Identity value generated by the INSERT just run on this cursor/session."""
        cur.execute("SELECT CAST(SCOPE_IDENTITY() AS INT)")
        return cur.fetchone()[0]

    @contextmanager
    def _transaction(self):
        """Yield a cursor; commit if the block succeeds, roll everything back if it raises."""
        self._ensure_connected()
        self.conn.autocommit = False
        cur = self.conn.cursor()
        try:
            yield cur
            self.conn.commit()
        except BaseException:
            try:
                self.conn.rollback()
            except pyodbc.Error:
                pass
            raise
        finally:
            cur.close()
            try:
                self.conn.autocommit = True
            except pyodbc.Error:
                pass

    # ---------------- categories ----------------
    def get_categories(self):
        """{"PC & Components": ["CPUs", "GPUs", ...], "Mobile & Accessories": [...]}"""
        rows = self._fetch("""
            SELECT d.name AS department, c.name AS category
            FROM categories c JOIN departments d ON d.id = c.department_id
            ORDER BY d.id, c.id
        """)
        out = {}
        for r in rows:
            out.setdefault(r["department"], []).append(r["category"])
        return out

    # ---------------- products ----------------
    def get_products(self):
        rows = self._fetch("""
            SELECT id, name, department, category, specs, price, rating,
                   icon, image_path AS image, stock
            FROM v_catalog
            ORDER BY category_id, name
        """)
        for r in rows:
            r["price"] = float(r["price"])
            r["rating"] = float(r["rating"])
        return rows

    def name_exists(self, name, exclude_id=None):
        rows = self._fetch("SELECT id FROM products WHERE name = ?", (name,))
        return any(r["id"] != exclude_id for r in rows)

    def add_product(self, category, name, specs, price, rating, icon, image, stock, extra=None):
        """Insert a product (and its monitor/accessory detail row) in one transaction."""
        with self._transaction() as cur:
            cur.execute("""
                INSERT INTO products (category_id, name, specs, price, rating, icon, image_path, stock)
                VALUES ((SELECT id FROM categories WHERE name = ?), ?, ?, ?, ?, ?, ?, ?)
            """, (category, name, specs, price, rating, icon, image, stock))
            product_id = self._new_id(cur)
            self._sync_extra_specs(cur, product_id, category, extra)
            return product_id

    def update_product(self, product_id, category, name, specs, price, rating, icon, image, stock, extra=None):
        """Update a product (and its detail row) in one transaction."""
        with self._transaction() as cur:
            cur.execute("""
                UPDATE products
                SET category_id = (SELECT id FROM categories WHERE name = ?),
                    name = ?, specs = ?, price = ?, rating = ?, icon = ?, image_path = ?, stock = ?,
                    updated_at = SYSDATETIME()
                WHERE id = ?
            """, (category, name, specs, price, rating, icon, image, stock, product_id))
            self._sync_extra_specs(cur, product_id, category, extra)

    def _sync_extra_specs(self, cur, product_id, category, extra):
        """Keep monitor_specs / monitor_accessory_specs consistent with the product's category.

        - A row of the wrong kind (e.g. product moved from Monitors to Cases) is removed.
        - If detail data is supplied for the right kind, the row is replaced.
        Runs on the caller's cursor, so it commits/rolls back together with the product.
        """
        if not self.specs_available:
            return
        kind = spec_kind(category)
        for k, table in SPEC_TABLES.items():
            if k != kind:
                cur.execute(f"DELETE FROM {table} WHERE product_id = ?", (product_id,))
        if kind and extra:
            table = SPEC_TABLES[kind]
            cols = [f[0] for f in SPEC_FIELDS[kind]]
            cur.execute(f"DELETE FROM {table} WHERE product_id = ?", (product_id,))
            cur.execute(
                f"INSERT INTO {table} (product_id, {', '.join(cols)}) "
                f"VALUES (?{', ?' * len(cols)})",
                [product_id] + [extra.get(c) for c in cols])

    def get_extra_specs(self, product_id, category):
        """Detail fields for a monitor/accessory product as {column: value}, or {} if none."""
        kind = spec_kind(category)
        if not kind or not self.specs_available:
            return {}
        rows = self._fetch(f"SELECT * FROM {SPEC_TABLES[kind]} WHERE product_id = ?", (product_id,))
        if not rows:
            return {}
        row = rows[0]
        out = {}
        for col, *_ in SPEC_FIELDS[kind]:
            v = row.get(col)
            out[col] = float(v) if hasattr(v, "as_tuple") else v   # Decimal -> float
        return out

    def delete_product(self, product_id):
        # order_items keeps its snapshot rows (product_id becomes NULL); spec rows cascade.
        self._execute("DELETE FROM products WHERE id = ?", (product_id,))

    # ---------------- auth ----------------
    def authenticate(self, username, password):
        """Return (user_id, role) on success, or None on bad username/password."""
        rows = self._fetch(
            "SELECT id, [role], password_hash FROM users WHERE username = ?", (username,))
        hashed = hash_password(password)
        for r in rows:
            if r["password_hash"] == hashed:
                return r["id"], r["role"]
        return None

    def register(self, username, full_name, email, password):
        """Return (user_id, None) on success, or (None, error_message)."""
        taken = "That username or email is already registered."
        if self._fetch("SELECT id FROM users WHERE username = ? OR email = ?", (username, email)):
            return None, taken
        try:
            with self._transaction() as cur:
                cur.execute("""
                    INSERT INTO users (username, full_name, email, password_hash, [role])
                    VALUES (?, ?, ?, ?, N'customer')
                """, (username, full_name, email, hash_password(password)))
                return self._new_id(cur), None
        except pyodbc.IntegrityError:
            return None, taken          # lost a race against another registration
        except pyodbc.Error as err:
            return None, friendly_error(err)

    # ---------------- checkout ----------------
    def checkout(self, user_id, cart_items):
        """Place an order atomically and decrement inventory (no database trigger involved)."""
        if not cart_items:
            return False, "Your cart is empty."

        # Combine repeated product IDs so stock is checked against the total quantity.
        quantities = {}
        for pid, qty in cart_items:
            try:
                pid, qty = int(pid), int(qty)
            except (TypeError, ValueError):
                return False, "Invalid item or quantity in your cart."
            if qty < 1:
                return False, "Item quantities must be at least 1."
            quantities[pid] = quantities.get(pid, 0) + qty

        ids = sorted(quantities)  # same lock order for everyone -> no deadlocks between shoppers
        placeholders = ",".join("?" * len(ids))
        try:
            with self._transaction() as cur:
                # UPDLOCK keeps the inventory rows locked until commit/rollback (SQL Server's
                # equivalent of SELECT ... FOR UPDATE), so two shoppers cannot oversell.
                cur.execute(f"""
                    SELECT p.id, p.name, p.price, p.stock, c.name AS category_name
                    FROM products AS p WITH (UPDLOCK, ROWLOCK)
                    JOIN categories AS c ON c.id = p.category_id
                    WHERE p.id IN ({placeholders})
                """, ids)
                by_id = {r["id"]: r for r in self._as_dicts(cur)}

                for pid in ids:
                    product = by_id.get(pid)
                    if product is None:
                        raise _CartError("An item in your cart is no longer available.")
                    if quantities[pid] > product["stock"]:
                        raise _CartError(f"Not enough stock for {product['name']}.")

                cur.execute("INSERT INTO orders (user_id, status) OUTPUT INSERTED.id VALUES (?, N'completed')", (user_id,))
                order_id = cur.fetchone()[0]
                total = 0

                for pid in ids:
                    product, qty = by_id[pid], quantities[pid]

                    # Conditional update is a second safeguard against overselling.
                    cur.execute("""
                        UPDATE products SET stock = stock - ?
                        WHERE id = ? AND stock >= ?
                    """, (qty, pid, qty))
                    if cur.rowcount != 1:
                        raise _CartError(
                            f"Stock changed while checking out {product['name']}. Please try again.")

                    cur.execute("""
                        INSERT INTO order_items
                            (order_id, product_id, product_name, category_name, quantity, unit_price)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (order_id, pid, product["name"], product["category_name"],
                          qty, product["price"]))
                    total += product["price"] * qty

                cur.execute("UPDATE orders SET total = ? WHERE id = ?", (total, order_id))
            return True, f"Order placed! Total: ${float(total):,.2f}\n(Demo checkout: no real payment.)"
        except _CartError as err:
            return False, str(err)
        except pyodbc.Error as err:
            return False, f"Checkout failed: {friendly_error(err)}"

    # ---------------- analytics ----------------
    def get_sales(self, period, category="All"):
        """period: 'Today' or 'This Week'. Reads from the v_sales view (completed orders only)."""
        if period == "Today":
            where = "sold_at >= CAST(SYSDATETIME() AS DATE)"
        else:
            where = "sold_at >= DATEADD(DAY, -7, SYSDATETIME())"
        params = []
        if category != "All":
            where += " AND category_name = ?"
            params.append(category)
        rows = self._fetch(f"""
            SELECT product_name AS name, category_name AS category, quantity AS qty,
                   unit_price, sold_at AS [timestamp]
            FROM v_sales WHERE {where}
        """, params)
        for r in rows:
            r["unit_price"] = float(r["unit_price"])
        return rows


# ============================================================
# SMALL UI HELPERS
# ============================================================
def green_btn(parent, text, command, **kw):
    kw.setdefault("height", 36)
    kw.setdefault("corner_radius", 9)
    return ctk.CTkButton(
        parent, text=text, command=command, fg_color=GREEN, hover_color=GREEN_HOVER,
        text_color=GREEN_TEXT, font=ctk.CTkFont(size=12, weight="bold"), **kw,
    )


def link_btn(parent, text, command, **kw):
    return ctk.CTkButton(
        parent, text=text, command=command, fg_color="transparent",
        hover_color=("#e8f0f8", "#1b2633"), text_color=(NEON_BLUE, NEON), **kw,
    )


def danger_btn(parent, text, command, **kw):
    return ctk.CTkButton(
        parent, text=text, command=command, fg_color="transparent",
        hover_color=("#ffe8e8", "#351d24"), text_color=("#c13f3f", "#ff7777"), **kw,
    )


def reset_scroll(scrollable):
    """Scroll a CTkScrollableFrame back to the top after its content was rebuilt."""
    try:
        scrollable.update_idletasks()
        canvas = scrollable._parent_canvas
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.yview_moveto(0)
    except Exception:
        pass


def shorten(text, n):
    return text if len(text) <= n else text[: n - 1] + "…"


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def find_image(product):
    """Return the image Path for a product, or None (emoji fallback is used)."""
    explicit = product.get("image")
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    for ext in IMAGE_EXTS:
        f = IMAGE_DIR / f"{slugify(product['name'])}{ext}"
        if f.is_file():
            return f
    return None


# ============================================================
# APPLICATION CONTROLLER
# ============================================================
class NexusTechStoreApp(ctk.CTk):
    """Owns all shared state and switches between screens."""

    def __init__(self):
        super().__init__()
        self.title("NEXUS TECH STORE")
        self.geometry("1200x750")
        self.resizable(False, False)
        self.center_window(1200, 750)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")

        # ---- database connection ----
        try:
            self.db = Database()
            self.categories = self.db.get_categories()
            self.products = self.db.get_products()
        except DBError as err:
            messagebox.showerror(
                "Database Connection Error",
                "Could not connect to the SQL Server database.\n\n"
                f"Server:   {DB_SERVER}\nDatabase: {DB_NAME}\n\n"
                "Check that:\n"
                "  1. the SQL Server service is running,\n"
                "  2. DB_SERVER in nexus_tech_store.py matches the server name used in SSMS,\n"
                "  3. nexus_tech_store.sql has been executed in SSMS.\n\n"
                f"Details: {friendly_error(err)}")
            self.destroy()
            raise SystemExit(1)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # ---- shared state ----
        self.cart = {}  # product id -> quantity
        self.current_user = None
        self.current_user_id = None
        self.current_role = None  # "admin" or "customer"
        self.dark_mode = True
        self.cart_window = None
        self.form_window = None
        self.image_cache = {}

        self.container = ctk.CTkFrame(self, corner_radius=0)
        self.container.pack(fill="both", expand=True)
        self.frames = {}
        for cls in (LoginFrame, RegisterFrame, LandingFrame, StoreFrame, AdminFrame):
            frame = cls(self.container, self)
            self.frames[cls.__name__] = frame
            frame.place(x=0, y=0, relwidth=1, relheight=1)

        self.show_frame("LoginFrame")

    # ---------------- window / navigation ----------------
    def on_close(self):
        self.db.close()
        self.destroy()

    def center_window(self, w, h):
        self.update_idletasks()
        x = max(0, (self.winfo_screenwidth() - w) // 2)
        y = max(0, (self.winfo_screenheight() - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def show_frame(self, name):
        if name == "AdminFrame" and self.current_role != "admin":
            return  # customers can never reach admin screens
        frame = self.frames[name]
        frame.tkraise()
        if hasattr(frame, "refresh"):
            frame.refresh()

    def refresh_all(self):
        self.frames["StoreFrame"].refresh()
        self.frames["AdminFrame"].refresh()
        if self.cart_window is not None and self.cart_window.winfo_exists():
            self.cart_window.render()

    # ---------------- auth ----------------
    def login(self):
        f = self.frames["LoginFrame"]
        user, pw = f.email_entry.get().strip(), f.password_entry.get()
        if not user or not pw:
            messagebox.showwarning("Missing Information", "Please enter your username/email and password.")
            return
        try:
            result = self.db.authenticate(user, pw)
        except DBError as err:
            messagebox.showerror("Database Error", f"Could not reach the database:\n{friendly_error(err)}")
            return
        if result is None:
            messagebox.showerror("Login Failed", "Invalid username or password.")
            return
        uid, role = result
        self.current_user, self.current_user_id, self.current_role = user, uid, role
        self.show_frame("AdminFrame" if role == "admin" else "LandingFrame")

    def register(self):
        f = self.frames["RegisterFrame"]
        name, email = f.name_entry.get().strip(), f.email_entry.get().strip()
        pw, confirm = f.password_entry.get(), f.confirm_entry.get()
        if not all([name, email, pw, confirm]):
            messagebox.showwarning("Missing Information", "Please complete every registration field.")
            return
        if pw != confirm:
            messagebox.showerror("Password Mismatch", "Password and confirmation do not match.")
            return
        if email.lower() == ADMIN_USER:
            messagebox.showerror("Not Allowed", "That username is reserved.")
            return
        try:
            uid, err = self.db.register(email, name, email, pw)
        except DBError as err:
            messagebox.showerror("Database Error", f"Could not reach the database:\n{friendly_error(err)}")
            return
        if err:
            messagebox.showerror("Registration Failed", err)
            return
        self.current_user, self.current_user_id, self.current_role = email, uid, "customer"
        messagebox.showinfo("Account Created", "Your NEXUS account has been created.")
        self.show_frame("LandingFrame")

    def logout(self):
        self.current_user, self.current_user_id, self.current_role = None, None, None
        self.cart.clear()
        for w in (self.cart_window, self.form_window):
            if w is not None and w.winfo_exists():
                w.destroy()
        self.cart_window = self.form_window = None
        login = self.frames["LoginFrame"]
        login.email_entry.delete(0, "end")
        login.password_entry.delete(0, "end")
        self.show_frame("LoginFrame")

    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        ctk.set_appearance_mode("dark" if self.dark_mode else "light")
        self.refresh_all()

    # ---------------- products ----------------
    def reload_products(self):
        self.products = self.db.get_products()

    def all_categories(self):
        return [c for cats in self.categories.values() for c in cats]

    def dept_of(self, category):
        for d, cats in self.categories.items():
            if category in cats:
                return d
        return ""

    def product_by_id(self, pid):
        return next((p for p in self.products if p["id"] == pid), None)

    def icon_for(self, name):
        return next((p["icon"] for p in self.products if p["name"] == name), "📦")

    def get_image(self, product, box=(210, 85)):
        """CTkImage scaled to fit inside `box`, or None if the product has no image file."""
        path = find_image(product)
        if path is None:
            return None
        key = (str(path), path.stat().st_mtime, box)
        if key not in self.image_cache:
            try:
                img = Image.open(path).convert("RGBA")
                w, h = img.size
                scale = min(box[0] / w, box[1] / h)
                self.image_cache[key] = ctk.CTkImage(
                    light_image=img, dark_image=img, size=(max(1, int(w * scale)), max(1, int(h * scale))))
            except Exception:
                self.image_cache[key] = None
        return self.image_cache[key]

    def delete_product(self, product):
        try:
            self.db.delete_product(product["id"])
        except DBError as err:
            messagebox.showerror("Database Error", f"Could not delete the product:\n{friendly_error(err)}")
            return
        self.cart.pop(product["id"], None)
        self.reload_products()
        self.refresh_all()

    # ---------------- cart ----------------
    def cart_count(self):
        return sum(self.cart.values())

    def cart_total(self):
        total = 0.0
        for pid, qty in self.cart.items():
            p = self.product_by_id(pid)
            if p:
                total += p["price"] * qty
        return total

    def change_qty(self, pid, delta):
        p = self.product_by_id(pid)
        if p is None:
            self.cart.pop(pid, None)
        else:
            new = self.cart.get(pid, 0) + delta
            if new > p["stock"]:
                messagebox.showinfo("Stock Limit", f"Only {p['stock']} of {p['name']} in stock.")
                return False
            if new <= 0:
                self.cart.pop(pid, None)
            else:
                self.cart[pid] = new
        self.frames["StoreFrame"].update_cart_button()
        if self.cart_window is not None and self.cart_window.winfo_exists():
            self.cart_window.render()
        return True

    def open_cart(self):
        w = self.cart_window
        if w is not None and w.winfo_exists():
            w.render()
            w.lift()
            w.focus()
            return
        self.cart_window = CartWindow(self)

    def checkout(self):
        if not self.cart:
            return False, "Your cart is empty."
        try:
            ok, msg = self.db.checkout(self.current_user_id, list(self.cart.items()))
        except DBError as err:
            return False, f"Could not reach the database:\n{friendly_error(err)}"
        if ok:
            self.cart.clear()
            self.reload_products()
            self.refresh_all()
        return ok, msg

    # ---------------- analytics helpers ----------------
    def get_sales(self, period, category="All"):
        try:
            return self.db.get_sales(period, category)
        except DBError as err:
            messagebox.showerror("Database Error", f"Could not load sales data:\n{friendly_error(err)}")
            return []

    @staticmethod
    def rank(sales, key):
        totals = defaultdict(int)
        for s in sales:
            totals[s[key]] += s["qty"]
        return sorted(totals.items(), key=lambda kv: kv[1], reverse=True)


# ============================================================
# LOGIN
# ============================================================
class LoginFrame(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent, corner_radius=0, fg_color=(LIGHT_BG, DARK_BG))
        self.controller = controller
        self.password_visible = False

        left = ctk.CTkFrame(self, width=570, corner_radius=0, fg_color=("#e8edf4", "#0c121a"))
        left.pack(side="left", fill="both")
        left.pack_propagate(False)
        right = ctk.CTkFrame(self, corner_radius=0, fg_color=(LIGHT_BG, DARK_BG))
        right.pack(side="left", fill="both", expand=True)

        ctk.CTkLabel(left, text="NEXUS", font=ctk.CTkFont(size=52, weight="bold"),
                     text_color=("#182332", NEON)).place(relx=0.12, rely=0.24)
        ctk.CTkLabel(left, text="TECH STORE", font=ctk.CTkFont(size=24, weight="bold"),
                     text_color=("#46566a", "#9db0c7")).place(relx=0.125, rely=0.34)
        ctk.CTkLabel(left, text="PERFORMANCE\nWITHOUT LIMITS", justify="left",
                     font=ctk.CTkFont(size=28, weight="bold"),
                     text_color=("#1e2d3e", "#e7f0fa")).place(relx=0.125, rely=0.49)
        ctk.CTkLabel(left, text="Rigs  •  Laptops  •  Components  •  Monitors\nPhones  •  Chargers  •  Cases  •  Audio",
                     justify="left", font=ctk.CTkFont(size=15),
                     text_color=("#5a6879", "#879ab0")).place(relx=0.125, rely=0.68)
        ctk.CTkLabel(left, text="◈ CPU    ◈ GPU    ◈ MONITORS    ◈ PHONES    ◈ AUDIO",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=(NEON_BLUE, NEON)).place(relx=0.125, rely=0.84)

        card = ctk.CTkFrame(right, width=410, height=520, corner_radius=24,
                            fg_color=(LIGHT_PANEL, DARK_PANEL), border_width=1,
                            border_color=(LIGHT_BORDER, DARK_BORDER))
        card.place(relx=0.5, rely=0.5, anchor="center")
        card.pack_propagate(False)

        ctk.CTkLabel(card, text="SIGN IN", font=ctk.CTkFont(size=29, weight="bold")).pack(pady=(45, 5))
        ctk.CTkLabel(card, text="Access your NEXUS workspace", text_color=MUTED).pack(pady=(0, 28))

        self.email_entry = ctk.CTkEntry(card, width=320, height=46, corner_radius=10,
                                        placeholder_text="Email or username")
        self.email_entry.pack(pady=8)

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(pady=8)
        self.password_entry = ctk.CTkEntry(row, width=270, height=46, corner_radius=10,
                                           placeholder_text="Password", show="•")
        self.password_entry.pack(side="left")
        self.password_entry.bind("<Return>", lambda e: controller.login())
        ctk.CTkButton(row, text="◉", width=44, height=46, corner_radius=10,
                      command=self.toggle_password).pack(side="left", padx=(6, 0))

        green_btn(card, "LOGIN", controller.login, width=320, height=48, corner_radius=10).pack(pady=(20, 12))
        link_btn(card, "New user? Create an account",
                 lambda: controller.show_frame("RegisterFrame")).pack()
        ctk.CTkLabel(card, text=f"Admin demo: {ADMIN_USER} / {ADMIN_PASS}",
                     font=ctk.CTkFont(size=11), text_color=MUTED).pack(pady=(18, 0))

    def toggle_password(self):
        self.password_visible = not self.password_visible
        self.password_entry.configure(show="" if self.password_visible else "•")


# ============================================================
# REGISTER
# ============================================================
class RegisterFrame(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent, corner_radius=0, fg_color=(LIGHT_BG, DARK_BG))
        self.controller = controller

        ctk.CTkLabel(self, text="NEXUS TECH STORE", font=ctk.CTkFont(size=34, weight="bold"),
                     text_color=("#1d2c3e", NEON)).pack(pady=(55, 5))
        ctk.CTkLabel(self, text="Create your NEXUS account",
                     font=ctk.CTkFont(size=23, weight="bold")).pack(pady=(0, 22))

        card = ctk.CTkFrame(self, width=440, height=470, corner_radius=24,
                            fg_color=(LIGHT_PANEL, DARK_PANEL), border_width=1,
                            border_color=(LIGHT_BORDER, DARK_BORDER))
        card.pack()
        card.pack_propagate(False)

        self.name_entry = self.make_entry(card, "Full name")
        self.email_entry = self.make_entry(card, "Email address")
        self.password_entry = self.make_entry(card, "Password", show="•")
        self.confirm_entry = self.make_entry(card, "Confirm password", show="•")

        green_btn(card, "CREATE ACCOUNT", controller.register, width=320, height=48,
                  corner_radius=10).pack(pady=(22, 12))
        link_btn(card, "← Back to Login", lambda: controller.show_frame("LoginFrame")).pack()

    @staticmethod
    def make_entry(parent, placeholder, show=None):
        e = ctk.CTkEntry(parent, width=320, height=44, corner_radius=10,
                         placeholder_text=placeholder, show=show)
        e.pack(pady=(26, 7) if placeholder == "Full name" else 7)
        return e


# ============================================================
# LANDING
# ============================================================
class LandingFrame(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent, corner_radius=0, fg_color=(LIGHT_BG, DARK_BG))
        self.controller = controller

        header = ctk.CTkFrame(self, height=78, corner_radius=0, fg_color=(LIGHT_PANEL, DARK_PANEL))
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(header, text="NEXUS TECH STORE", font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=("#1d2c3e", NEON)).pack(side="left", padx=40)
        ctk.CTkButton(header, text="Enter Store →", width=130, height=38, corner_radius=19,
                      command=lambda: controller.show_frame("StoreFrame")).pack(side="right", padx=(0, 35))
        danger_btn(header, "Logout", controller.logout, width=70).pack(side="right", padx=5)

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(content, text="NEXUS PERFORMANCE LAB", font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=(NEON_BLUE, NEON)).pack(pady=(0, 14))
        ctk.CTkLabel(content, text="Everything Tech, One Store:\nRigs, Monitors, Phones & Accessories",
                     font=ctk.CTkFont(size=38, weight="bold"), justify="center").pack()
        ctk.CTkLabel(content, text="Gaming hardware, monitors and desk gear, creator machines, mobile devices, power and audio.",
                     font=ctk.CTkFont(size=17), text_color=("gray40", "gray65")).pack(pady=24)
        green_btn(content, "SHOP NOW  →", lambda: controller.show_frame("StoreFrame"),
                  width=240, height=55, corner_radius=27).pack()

        bar = ctk.CTkFrame(self, width=850, height=100, corner_radius=20, fg_color=("#e5ebf2", "#101923"),
                           border_width=1, border_color=(LIGHT_BORDER, DARK_BORDER))
        bar.place(relx=0.5, rely=0.84, anchor="center")
        bar.pack_propagate(False)
        for icon, text in [("⚡", "Extreme Performance"), ("🎮", "Gaming Ready"), ("🖥️", "Monitors & Gear"),
                           ("📱", "Latest Mobile"), ("🎧", "Premium Audio")]:
            block = ctk.CTkFrame(bar, fg_color="transparent")
            block.pack(side="left", expand=True, fill="both")
            ctk.CTkLabel(block, text=icon, font=ctk.CTkFont(size=24)).pack(pady=(17, 2))
            ctk.CTkLabel(block, text=text, font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=("gray35", "gray70")).pack()


# ============================================================
# SHARED CATEGORY SIDEBAR
# ============================================================
class CategorySidebar(ctk.CTkScrollableFrame):
    """Scrollable so the list still fits the fixed-size window as categories are added."""

    def __init__(self, parent, controller, on_select):
        super().__init__(parent, width=192, corner_radius=0, fg_color=("#edf1f6", "#0d141c"))
        self.on_select = on_select
        self.buttons = {}
        self._add("All Products", "All", (16, 2))
        for dept, cats in controller.categories.items():
            ctk.CTkLabel(self, text=dept.upper(), font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=("gray35", "#93a5ba")).pack(anchor="w", padx=20, pady=(16, 4))
            for cat in cats:
                self._add(cat, cat, 1)

    def _add(self, text, key, pady):
        btn = ctk.CTkButton(self, text=text, height=32, anchor="w", fg_color="transparent",
                            hover_color=("#dfe8f1", "#1a2837"), text_color=("#243447", "#d3deeb"),
                            command=lambda k=key: self.on_select(k))
        btn.pack(fill="x", padx=10, pady=pady)
        self.buttons[key] = btn

    def highlight(self, selected):
        for key, btn in self.buttons.items():
            btn.configure(fg_color=("#cdeedd", "#163a29") if key == selected else "transparent")


# ============================================================
# CUSTOMER STORE
# ============================================================
class StoreFrame(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent, corner_radius=0, fg_color=(LIGHT_BG, DARK_BG))
        self.controller = controller
        self.selected_category = "All"
        self.build_ui()

    def build_ui(self):
        c = self.controller
        header = ctk.CTkFrame(self, height=72, corner_radius=0, fg_color=(LIGHT_PANEL, DARK_PANEL),
                              border_width=1, border_color=(LIGHT_BORDER, DARK_BORDER))
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(header, text="NEXUS", font=ctk.CTkFont(size=21, weight="bold"),
                     text_color=("#182838", NEON)).pack(side="left", padx=(20, 4))
        ctk.CTkLabel(header, text="TECH STORE", font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=("gray40", "#a6b6c9")).pack(side="left", padx=(0, 20))
        self.search_entry = ctk.CTkEntry(header, width=380, height=40, corner_radius=10,
                                         placeholder_text="Search GPUs, monitors, phones, audio...")
        self.search_entry.pack(side="left", padx=7)
        self.search_entry.bind("<KeyRelease>", lambda e: self.refresh_products())

        self.theme_switch = ctk.CTkSwitch(header, text="", width=45, command=c.toggle_theme)
        self.theme_switch.pack(side="right", padx=(5, 15))
        danger_btn(header, "Logout", c.logout, width=65, height=34).pack(side="right", padx=4)
        self.cart_button = green_btn(header, "🛒 0", c.open_cart, width=80, height=34, corner_radius=17)
        self.cart_button.pack(side="right", padx=5)
        self.admin_btn = ctk.CTkButton(header, text="← Admin Panel", width=110, height=34,
                                       command=lambda: c.show_frame("AdminFrame"))

        body = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        body.pack(fill="both", expand=True)
        self.sidebar = CategorySidebar(body, c, self.set_category)
        self.sidebar.pack(side="left", fill="y")
        main = ctk.CTkFrame(body, fg_color="transparent", corner_radius=0)
        main.pack(side="left", fill="both", expand=True)

        # ---- customer-facing "Best Sellers" analytics strip ----
        self.trend_period = "Today"
        self.trend_panel = ctk.CTkFrame(main, height=150, corner_radius=16, fg_color=(LIGHT_PANEL, DARK_PANEL),
                                        border_width=1, border_color=(LIGHT_BORDER, DARK_BORDER))
        self.trend_panel.pack(fill="x", padx=14, pady=(12, 0))
        self.trend_panel.pack_propagate(False)
        top = ctk.CTkFrame(self.trend_panel, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(10, 0))
        self.trend_title = ctk.CTkLabel(top, text="", font=ctk.CTkFont(size=16, weight="bold"))
        self.trend_title.pack(side="left")
        self.trend_toggle = ctk.CTkSegmentedButton(top, values=["Today", "This Week"],
                                                   command=self.set_trend_period)
        self.trend_toggle.set(self.trend_period)
        self.trend_toggle.pack(side="right")
        self.trend_row = ctk.CTkFrame(self.trend_panel, fg_color="transparent")
        self.trend_row.pack(fill="both", expand=True, padx=10, pady=(6, 8))

        self.product_area = ctk.CTkScrollableFrame(main, corner_radius=0, fg_color=(LIGHT_BG, DARK_BG))
        self.product_area.pack(fill="both", expand=True)
        self.refresh_trending()
        self.refresh_products()

    def set_category(self, category):
        self.selected_category = category
        self.sidebar.highlight(category)
        self.refresh_trending()
        self.refresh_products()

    def refresh(self):
        c = self.controller
        if c.dark_mode:
            self.theme_switch.select()
        else:
            self.theme_switch.deselect()
        self.admin_btn.pack_forget()
        if c.current_role == "admin":
            self.admin_btn.pack(side="right", padx=8)
        self.sidebar.highlight(self.selected_category)
        self.update_cart_button()
        self.refresh_trending()
        self.refresh_products()

    def set_trend_period(self, period):
        self.trend_period = period
        self.refresh_trending()

    def refresh_trending(self):
        """Top 5 best sellers for the selected category (units only, no revenue for customers)."""
        c = self.controller
        cat = self.selected_category
        scope = "All Categories" if cat == "All" else cat
        self.trend_title.configure(text=f"🔥 Best Sellers · {scope}")
        for w in self.trend_row.winfo_children():
            w.destroy()

        ranked = c.rank(c.get_sales(self.trend_period, cat), "name")[:5]
        if not ranked:
            when = "today" if self.trend_period == "Today" else "this week"
            ctk.CTkLabel(self.trend_row, text=f"No sales yet {when} in {scope}.",
                         text_color=MUTED).pack(expand=True)
            return
        for i, (name, sold) in enumerate(ranked):
            card = ctk.CTkFrame(self.trend_row, corner_radius=12, fg_color=(LIGHT_BG, DARK_BG))
            card.pack(side="left", fill="both", expand=True, padx=4)
            ctk.CTkLabel(card, text=f"#{i + 1}", font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=GREEN).pack(anchor="w", padx=10, pady=(6, 0))
            ctk.CTkLabel(card, text=f"{c.icon_for(name)} {shorten(name, 17)}",
                         font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=10)
            ctk.CTkLabel(card, text=f"{sold} sold", text_color=MUTED).pack(anchor="w", padx=10, pady=(0, 6))

    def refresh_products(self):
        for w in self.product_area.winfo_children():
            w.destroy()
        reset_scroll(self.product_area)
        q = self.search_entry.get().strip().lower()
        items = [
            p for p in self.controller.products
            if (self.selected_category == "All" or p["category"] == self.selected_category)
            and (not q or q in p["name"].lower() or q in p["category"].lower() or q in p["specs"].lower())
        ]

        title_row = ctk.CTkFrame(self.product_area, fg_color="transparent")
        title_row.grid(row=0, column=0, columnspan=3, sticky="ew", padx=10, pady=(10, 15))
        title = "Featured Tech" if self.selected_category == "All" else self.selected_category
        ctk.CTkLabel(title_row, text=title, font=ctk.CTkFont(size=27, weight="bold")).pack(side="left")
        ctk.CTkLabel(title_row, text=f"{len(items)} products", text_color=MUTED).pack(
            side="left", padx=12, pady=(7, 0))

        for i, p in enumerate(items):
            self.create_card(p, i // 3 + 1, i % 3)
        for col in range(3):
            self.product_area.grid_columnconfigure(col, weight=1)

    def create_card(self, p, row, col):
        card = ctk.CTkFrame(self.product_area, width=260, height=290, corner_radius=18,
                            fg_color=(LIGHT_CARD, DARK_CARD), border_width=1,
                            border_color=(LIGHT_BORDER, DARK_BORDER))
        card.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")
        card.grid_propagate(False)

        photo = self.controller.get_image(p)
        box = ctk.CTkFrame(card, width=230, height=95, corner_radius=14,
                           fg_color="#ffffff" if photo else ("#eaf0f6", "#1a2633"))
        box.pack(padx=14, pady=(14, 8))
        box.pack_propagate(False)
        if photo:
            ctk.CTkLabel(box, text="", image=photo).pack(expand=True)
        else:
            ctk.CTkLabel(box, text=p["icon"], font=ctk.CTkFont(size=44)).pack(expand=True)

        ctk.CTkLabel(card, text=p["name"], font=ctk.CTkFont(size=15, weight="bold"),
                     anchor="w").pack(fill="x", padx=17)
        ctk.CTkLabel(card, text=p["specs"], font=ctk.CTkFont(size=11), text_color=MUTED,
                     anchor="w", justify="left", wraplength=225).pack(fill="x", padx=17, pady=(3, 0))

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(fill="x", padx=17, pady=(6, 4))
        ctk.CTkLabel(info, text=f"${p['price']:,.2f}", font=ctk.CTkFont(size=17, weight="bold")).pack(side="left")
        ctk.CTkLabel(info, text=f"★ {p['rating']}", text_color=("#c48400", "#ffbf3d")).pack(side="right")

        if p["stock"] <= 0:
            ctk.CTkButton(card, text="OUT OF STOCK", height=36, state="disabled",
                          corner_radius=9).pack(fill="x", padx=17, pady=(2, 12))
        else:
            green_btn(card, "ADD TO CART", lambda pr=p: self.add_to_cart(pr)).pack(
                fill="x", padx=17, pady=(2, 12))

    def add_to_cart(self, product):
        self.controller.change_qty(product["id"], 1)

    def update_cart_button(self):
        self.cart_button.configure(text=f"🛒 {self.controller.cart_count()}")


# ============================================================
# CART WINDOW
# ============================================================
class CartWindow(ctk.CTkToplevel):
    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.title("Shopping Cart")
        self.geometry("500x560")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.close)

        ctk.CTkLabel(self, text="🛒 Your Cart", font=ctk.CTkFont(size=22, weight="bold")).pack(
            anchor="w", padx=20, pady=(18, 4))
        self.list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list_frame.pack(fill="both", expand=True, padx=14, pady=6)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=20, pady=(4, 16))
        self.total_label = ctk.CTkLabel(footer, text="", font=ctk.CTkFont(size=18, weight="bold"))
        self.total_label.pack(side="left")
        green_btn(footer, "CHECKOUT", self.do_checkout, width=140, height=40).pack(side="right")

        self.render()
        self.after(150, self._front)

    def _front(self):
        if self.winfo_exists():
            self.lift()
            self.focus()

    def render(self):
        c = self.controller
        for w in self.list_frame.winfo_children():
            w.destroy()
        if not c.cart:
            ctk.CTkLabel(self.list_frame, text="Your cart is empty.", text_color=MUTED).pack(pady=40)
        for pid, qty in list(c.cart.items()):
            p = c.product_by_id(pid)
            if p is None:
                continue
            row = ctk.CTkFrame(self.list_frame, corner_radius=10, fg_color=(LIGHT_CARD, DARK_CARD))
            row.pack(fill="x", pady=4)
            cart_photo = self.controller.get_image(p, box=(40, 40))
            if cart_photo:
                ctk.CTkLabel(row, text="", image=cart_photo, width=40).pack(side="left", padx=(8, 2))
            else:
                ctk.CTkLabel(row, text=p["icon"], font=ctk.CTkFont(size=24), width=40).pack(side="left", padx=(8, 2))
            text = ctk.CTkFrame(row, fg_color="transparent")
            text.pack(side="left", fill="x", expand=True, pady=6)
            ctk.CTkLabel(text, text=shorten(p["name"], 24), font=ctk.CTkFont(weight="bold"), anchor="w").pack(fill="x")
            ctk.CTkLabel(text, text=f"${p['price'] * qty:,.2f}", text_color=MUTED, anchor="w").pack(fill="x")
            danger_btn(row, "✕", lambda i=pid: c.change_qty(i, -c.cart.get(i, 0)), width=30).pack(side="right", padx=4)
            ctk.CTkButton(row, text="+", width=28, command=lambda i=pid: c.change_qty(i, 1)).pack(side="right", padx=2)
            ctk.CTkLabel(row, text=str(qty), width=26).pack(side="right")
            ctk.CTkButton(row, text="−", width=28, command=lambda i=pid: c.change_qty(i, -1)).pack(side="right", padx=2)
        self.total_label.configure(text=f"Total: ${c.cart_total():,.2f}")

    def do_checkout(self):
        ok, msg = self.controller.checkout()
        if ok:
            messagebox.showinfo("Checkout", msg, parent=self)
            self.close()
        else:
            messagebox.showwarning("Checkout", msg, parent=self)

    def close(self):
        self.controller.cart_window = None
        self.destroy()


# ============================================================
# ADMIN: PRODUCT FORM (add / edit)
# ============================================================
class ProductForm(ctk.CTkToplevel):
    def __init__(self, controller, product=None, default_category=None):
        super().__init__(controller)
        self.controller = controller
        self.product = product
        self.title("Edit Product" if product else "Add New Product")
        self.geometry("440x740")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.close)

        ctk.CTkLabel(self, text=self.title(), font=ctk.CTkFont(size=22, weight="bold")).pack(pady=(16, 4))

        # Buttons are packed first (at the bottom) so they stay visible; the fields scroll.
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(side="bottom", fill="x", pady=(0, 10))
        green_btn(footer, "SAVE PRODUCT", self.save, width=340, height=40).pack(pady=(8, 4))
        link_btn(footer, "Cancel", self.close).pack()
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=8)

        p = product or {}
        db = controller.db
        self.image_src = None
        self.extra_widgets = {}
        # Existing monitor/accessory details (only used while the category kind is unchanged).
        self.initial_kind = spec_kind(p.get("category")) if product else None
        self.initial_extra = {}
        if product and db.specs_available:
            try:
                self.initial_extra = db.get_extra_specs(product["id"], product["category"])
            except DBError:
                self.initial_extra = {}

        self.name_e = self.field("Product name", p.get("name", ""))
        cats = controller.all_categories()
        ctk.CTkLabel(self.body, text="Category", anchor="w").pack(fill="x", padx=20, pady=(8, 0))
        self.cat_menu = ctk.CTkOptionMenu(self.body, values=cats, width=340, command=self.on_category_change)
        self.cat_menu.set(p.get("category") or (default_category if default_category in cats else cats[0]))
        self.cat_menu.pack(pady=(2, 0))
        self.specs_label = ctk.CTkLabel(self.body, text="Specs", anchor="w")
        self.specs_label.pack(fill="x", padx=20, pady=(8, 0))
        self.specs_e = ctk.CTkEntry(self.body, width=340, height=36)
        self.specs_e.insert(0, p.get("specs", ""))
        self.specs_e.pack(pady=(2, 0))
        self.price_e = self.field("Price ($)", str(p.get("price", "")))
        self.rating_e = self.field("Rating (0-5)", str(p.get("rating", "4.5")))
        self.icon_e = self.field("Emoji icon", p.get("icon", "📦"))
        self.stock_e = self.field("Stock", str(p.get("stock", "10")))

        # Monitor / accessory detail fields appear here for those categories only.
        self.extra_frame = ctk.CTkFrame(self.body, fg_color="transparent")
        self.extra_frame.pack(fill="x")
        self.on_category_change(self.cat_menu.get())

        ctk.CTkLabel(self.body, text="Product image (optional)", anchor="w").pack(fill="x", padx=20, pady=(8, 0))
        img_row = ctk.CTkFrame(self.body, fg_color="transparent")
        img_row.pack(fill="x", padx=20, pady=(2, 10))
        ctk.CTkButton(img_row, text="Choose image…", width=130, command=self.choose_image).pack(side="left")
        current = find_image(p) if product else None
        self.image_label = ctk.CTkLabel(
            img_row, text=shorten(current.name, 22) if current else "none (emoji is used)", text_color=MUTED)
        self.image_label.pack(side="left", padx=8)
        self.after(150, self._front)

    def _front(self):
        if self.winfo_exists():
            self.lift()
            self.focus()

    def choose_image(self):
        path = filedialog.askopenfilename(
            parent=self, title="Choose product image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp")])
        if path:
            self.image_src = path
            self.image_label.configure(text=shorten(Path(path).name, 22))
        self.lift()

    def field(self, label, value):
        ctk.CTkLabel(self.body, text=label, anchor="w").pack(fill="x", padx=20, pady=(8, 0))
        e = ctk.CTkEntry(self.body, width=340, height=36)
        e.insert(0, value)
        e.pack(pady=(2, 0))
        return e

    # ---------------- monitor / accessory detail fields ----------------
    def active_kind(self):
        """'monitor' / 'accessory' when the chosen category has detail tables AND they exist in the DB."""
        kind = spec_kind(self.cat_menu.get())
        return kind if kind and self.controller.db.specs_available else None

    def on_category_change(self, category):
        kind = self.active_kind()
        for w in self.extra_frame.winfo_children():
            w.destroy()
        self.extra_widgets = {}
        if not kind:
            self.specs_label.configure(text="Specs")
            return
        self.specs_label.configure(text="Specs (leave blank to auto-generate)")
        saved = self.initial_extra if kind == self.initial_kind else {}
        title = "Monitor details" if kind == "monitor" else "Accessory details"
        ctk.CTkLabel(self.extra_frame, text=title, anchor="w",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(fill="x", padx=20, pady=(14, 0))
        for key, label, ftype, required, extra in SPEC_FIELDS[kind]:
            value = saved.get(key)
            if ftype == "bool":
                w = ctk.CTkSwitch(self.extra_frame, text=label)
                if value:
                    w.select()
                w.pack(anchor="w", padx=20, pady=(10, 0))
            else:
                ctk.CTkLabel(self.extra_frame, text=label + (" *" if required else ""),
                             anchor="w").pack(fill="x", padx=20, pady=(8, 0))
                if ftype == "choice":
                    w = ctk.CTkOptionMenu(self.extra_frame, values=list(extra), width=340)
                    w.set(value if value in extra else extra[0])
                else:
                    w = ctk.CTkEntry(self.extra_frame, width=340, height=36)
                    shown = f"{value:g}" if isinstance(value, float) else ("" if value is None else str(value))
                    w.insert(0, shown or extra)
                w.pack(pady=(2, 0))
            self.extra_widgets[key] = (ftype, required, label, w)

    def collect_extra(self, kind):
        """Validate and return ({column: value}, None) or (None, error message)."""
        data = {}
        for key, (ftype, required, label, w) in self.extra_widgets.items():
            clean_label = label.replace(" (optional)", "")
            if ftype == "bool":
                data[key] = 1 if w.get() else 0
                continue
            raw = w.get().strip()
            if ftype == "choice":
                data[key] = raw
                continue
            if not raw:
                if required:
                    return None, f"{clean_label} is required."
                data[key] = None
                continue
            try:
                if ftype == "text":
                    data[key] = raw
                elif ftype == "int":
                    data[key] = int(raw)
                    if data[key] <= 0:
                        raise ValueError
                elif ftype == "float":
                    data[key] = float(raw)
                    if data[key] <= 0:
                        raise ValueError
                elif ftype == "resolution":
                    norm = re.sub(r"\s+", "", raw.lower().replace("×", "x"))
                    if not re.fullmatch(r"\d+x\d+", norm):
                        raise ValueError
                    data[key] = norm
                elif ftype == "ratio":
                    norm = raw.replace(" ", "")
                    if not re.fullmatch(r"\d+(\.\d+)?:\d+(\.\d+)?", norm):
                        raise ValueError
                    data[key] = norm
            except ValueError:
                hints = {"int": "a whole number greater than 0", "float": "a number greater than 0",
                         "resolution": "like 2560x1440", "ratio": "like 16:9"}
                return None, f"{clean_label} must be {hints.get(ftype, 'valid')}."
        return data, None

    def save(self):
        c = self.controller
        name, specs, icon = self.name_e.get().strip(), self.specs_e.get().strip(), self.icon_e.get().strip()
        kind = self.active_kind()
        if not name or not icon or (not specs and not kind):
            messagebox.showerror("Invalid Input", "Name, specs, and icon are required.", parent=self)
            return
        try:
            price = float(self.price_e.get())
            if price <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid Input", "Price must be a number greater than 0.", parent=self)
            return
        try:
            stock = int(self.stock_e.get())
            if stock < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid Input", "Stock must be a whole number, 0 or more.", parent=self)
            return
        try:
            rating = float(self.rating_e.get())
            if not 0 <= rating <= 5:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid Input", "Rating must be between 0 and 5.", parent=self)
            return

        extra = None
        if kind:
            extra, error = self.collect_extra(kind)
            if error:
                messagebox.showerror("Invalid Input", error, parent=self)
                return
            if not specs:
                specs = build_specs_summary(kind, extra)

        try:
            exclude_id = self.product["id"] if self.product else None
            if c.db.name_exists(name, exclude_id=exclude_id):
                messagebox.showerror("Duplicate", "A product with that name already exists.", parent=self)
                return
        except DBError as err:
            messagebox.showerror("Database Error", f"Could not reach the database:\n{friendly_error(err)}", parent=self)
            return

        category = self.cat_menu.get()
        image_path = self.product.get("image") if self.product else None
        if self.image_src:
            try:
                IMAGE_DIR.mkdir(exist_ok=True)
                dest = IMAGE_DIR / f"{slugify(name)}{Path(self.image_src).suffix.lower()}"
                if Path(self.image_src).resolve() != dest.resolve():
                    shutil.copyfile(self.image_src, dest)
                image_path = str(dest)
            except OSError as err:
                messagebox.showerror("Image Error", f"Could not copy the image: {friendly_error(err)}", parent=self)
                return

        try:
            if self.product:
                c.db.update_product(self.product["id"], category, name, specs, price, rating,
                                     icon, image_path, stock, extra=extra)
            else:
                c.db.add_product(category, name, specs, price, rating, icon, image_path, stock, extra=extra)
        except DBError as err:
            messagebox.showerror("Database Error", f"Could not save the product:\n{friendly_error(err)}", parent=self)
            return

        c.reload_products()
        c.refresh_all()
        self.close()

    def close(self):
        self.controller.form_window = None
        self.destroy()


# ============================================================
# ADMIN DASHBOARD
# ============================================================
class AdminFrame(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent, corner_radius=0, fg_color=(LIGHT_BG, DARK_BG))
        self.controller = controller
        self.selected_category = "All"
        self.period = "Today"
        c = controller

        # ---- top header ----
        header = ctk.CTkFrame(self, height=64, corner_radius=0, fg_color=(LIGHT_PANEL, DARK_PANEL),
                              border_width=1, border_color=(LIGHT_BORDER, DARK_BORDER))
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(header, text="NEXUS TECH STORE", font=ctk.CTkFont(size=20, weight="bold"),
                     text_color=("#182838", NEON)).pack(side="left", padx=(20, 8))
        ctk.CTkLabel(header, text=" ADMIN ", font=ctk.CTkFont(size=11, weight="bold"), corner_radius=6,
                     fg_color=GREEN, text_color=GREEN_TEXT).pack(side="left")
        self.theme_switch = ctk.CTkSwitch(header, text="", width=45, command=c.toggle_theme)
        self.theme_switch.pack(side="right", padx=(5, 15))
        danger_btn(header, "Logout", c.logout, width=65, height=34).pack(side="right", padx=4)
        ctk.CTkButton(header, text="View Store →", width=110, height=34,
                      command=lambda: c.show_frame("StoreFrame")).pack(side="right", padx=8)

        # ---- body: sidebar + main ----
        body = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        body.pack(fill="both", expand=True)
        self.sidebar = CategorySidebar(body, c, self.set_category)
        self.sidebar.pack(side="left", fill="y")
        main = ctk.CTkFrame(body, fg_color="transparent")
        main.pack(side="left", fill="both", expand=True)

        # analytics panel (top of main area)
        self.analytics = ctk.CTkFrame(main, height=250, corner_radius=16, fg_color=(LIGHT_PANEL, DARK_PANEL),
                                      border_width=1, border_color=(LIGHT_BORDER, DARK_BORDER))
        self.analytics.pack(fill="x", padx=14, pady=(12, 6))
        self.analytics.pack_propagate(False)
        top = ctk.CTkFrame(self.analytics, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(10, 0))
        self.analytics_title = ctk.CTkLabel(top, text="", font=ctk.CTkFont(size=16, weight="bold"))
        self.analytics_title.pack(side="left")
        self.period_toggle = ctk.CTkSegmentedButton(top, values=["Today", "This Week"], command=self.set_period)
        self.period_toggle.set(self.period)
        self.period_toggle.pack(side="right")
        self.chart_area = ctk.CTkFrame(self.analytics, fg_color="transparent")
        self.chart_area.pack(fill="both", expand=True, padx=12, pady=(4, 8))

        # product list header + list
        lh = ctk.CTkFrame(main, fg_color="transparent")
        lh.pack(fill="x", padx=18, pady=(6, 2))
        self.list_title = ctk.CTkLabel(lh, text="", font=ctk.CTkFont(size=18, weight="bold"))
        self.list_title.pack(side="left")
        green_btn(lh, "+ Add New Product", self.open_form, width=160, height=34).pack(side="right")
        self.list_area = ctk.CTkScrollableFrame(main, fg_color=(LIGHT_BG, DARK_BG))
        self.list_area.pack(fill="both", expand=True, padx=8, pady=(0, 6))

    # ---------------- events ----------------
    def set_category(self, category):
        self.selected_category = category
        self.refresh()

    def set_period(self, period):
        self.period = period
        self.refresh_analytics()

    def open_form(self, product=None):
        c = self.controller
        w = c.form_window
        if w is not None and w.winfo_exists():
            w.lift()
            w.focus()
            return
        c.form_window = ProductForm(c, product, self.selected_category)

    def confirm_delete(self, product):
        if messagebox.askyesno("Delete Product", f"Delete '{product['name']}'? This cannot be undone."):
            self.controller.delete_product(product)

    # ---------------- refresh ----------------
    def refresh(self):
        if self.controller.dark_mode:
            self.theme_switch.select()
        else:
            self.theme_switch.deselect()
        self.sidebar.highlight(self.selected_category)
        self.refresh_analytics()
        self.refresh_list()

    def refresh_analytics(self):
        c = self.controller
        cat = self.selected_category
        scope = "Whole Store" if cat == "All" else cat
        self.analytics_title.configure(text=f"📊 Sales Analytics · {scope}")
        for w in self.chart_area.winfo_children():
            w.destroy()

        sales = c.get_sales(self.period, cat)
        if not sales:
            ctk.CTkLabel(self.chart_area, text=f"No sales recorded {self.period.lower()} for {scope}.",
                         text_color=MUTED).pack(expand=True)
            return

        units = sum(s["qty"] for s in sales)
        revenue = sum(s["qty"] * s["unit_price"] for s in sales)
        by_product = c.rank(sales, "name")
        by_cat = c.rank(sales, "category")

        kpis = ctk.CTkFrame(self.chart_area, fg_color="transparent", width=380)
        kpis.pack(side="left", fill="y", padx=(0, 10))
        kpis.grid_columnconfigure((0, 1), weight=1)
        for i, (label, value) in enumerate([
            ("UNITS SOLD", f"{units:,}"),
            ("REVENUE", f"${revenue:,.0f}"),
            ("TOP DEVICE", shorten(by_product[0][0], 17)),
            ("TOP CATEGORY", by_cat[0][0]),
        ]):
            card = ctk.CTkFrame(kpis, corner_radius=12, width=180, fg_color=(LIGHT_BG, DARK_BG))
            card.grid(row=i // 2, column=i % 2, padx=4, pady=4, sticky="nsew")
            ctk.CTkLabel(card, text=label, font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=MUTED).pack(anchor="w", padx=12, pady=(10, 0))
            ctk.CTkLabel(card, text=value, font=ctk.CTkFont(size=15, weight="bold")).pack(
                anchor="w", padx=12, pady=(0, 10))

        chart = ctk.CTkFrame(self.chart_area, fg_color="transparent")
        chart.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(chart, text="Top-selling devices", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=MUTED, anchor="w").pack(fill="x", pady=(2, 4))
        top5 = by_product[:5]
        peak = top5[0][1]
        for i, (name, sold) in enumerate(top5):
            row = ctk.CTkFrame(chart, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=f"{c.icon_for(name)} {shorten(name, 20)}", width=200, anchor="w").pack(side="left")
            bar = ctk.CTkProgressBar(row, height=12, progress_color=GREEN if i == 0 else BAR_BLUE)
            bar.set(sold / peak)
            bar.pack(side="left", fill="x", expand=True, padx=8)
            ctk.CTkLabel(row, text=f"{sold} sold", width=60, anchor="e").pack(side="left")

    def refresh_list(self):
        c = self.controller
        cat = self.selected_category
        for w in self.list_area.winfo_children():
            w.destroy()
        reset_scroll(self.list_area)   # stale scroll position hides the rows on some machines
        items = [p for p in c.products if cat == "All" or p["category"] == cat]
        self.list_title.configure(
            text=f"{'All Products' if cat == 'All' else cat}  ({len(items)})")

        for p in items:
            try:
                self._draw_row(p)
            except Exception as err:   # one bad product must not blank the whole list
                print(f"[admin list] could not draw product {p.get('id')} {p.get('name')!r}: {err!r}")
        self.after(50, lambda: reset_scroll(self.list_area))

    def _draw_row(self, p):
        if True:
            row = ctk.CTkFrame(self.list_area, corner_radius=10, fg_color=(LIGHT_CARD, DARK_CARD))
            row.pack(fill="x", padx=6, pady=3)
            admin_photo = self.controller.get_image(p, box=(44, 44))
            if admin_photo:
                ctk.CTkLabel(row, text="", image=admin_photo, width=44).pack(side="left", padx=(8, 2), pady=6)
            else:
                ctk.CTkLabel(row, text=p["icon"], font=ctk.CTkFont(size=22), width=44).pack(side="left", padx=(8, 2), pady=6)
            ctk.CTkLabel(row, text=shorten(p["name"], 40), font=ctk.CTkFont(weight="bold"),
                         width=340, anchor="w", text_color=("gray10", "gray90")).pack(side="left")
            ctk.CTkLabel(row, text=f"${p['price']:,.2f}", width=90, anchor="w",
                         text_color=("gray10", "gray90")).pack(side="left")
            low = p["stock"] <= 5
            ctk.CTkLabel(row, text=f"Stock: {p['stock']}", width=80, anchor="w",
                         text_color=("#c13f3f", "#ff7777") if low else MUTED).pack(side="left")
            ctk.CTkLabel(row, text=f"★ {p['rating']}", width=50, anchor="w",
                         text_color=("#c48400", "#ffbf3d")).pack(side="left")
            danger_btn(row, "Delete", lambda pr=p: self.confirm_delete(pr), width=60, height=30).pack(side="right", padx=(0, 8))
            link_btn(row, "Edit", lambda pr=p: self.open_form(pr), width=50, height=30).pack(side="right")


# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    app = NexusTechStoreApp()
    app.mainloop()