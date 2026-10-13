NEXUS TECH STORE - SQL SERVER (SSMS) VERSION

WHAT CHANGED
  * db.py is gone. The whole database layer (connection, queries, checkout, monitor/accessory
    specs) now lives in nexus_tech_store.py, section "DATABASE LAYER". There is exactly one
    connection object in the program (NexusTechStoreApp.db), so nothing can conflict.
  * MySQL/XAMPP (mysql-connector) was replaced by Microsoft SQL Server (pyodbc).
  * nexus_tech_store.sql was rewritten in T-SQL for SSMS. Same tables, same columns, same demo data:
        users, departments, categories, products, orders, order_items,
        monitor_specs, monitor_accessory_specs
        views: v_sales, v_catalog, v_top_sellers_today, v_top_sellers_week,
               v_monitor_catalog, v_accessory_catalog
  * At start-up the app checks that every table, view and column it needs exists. If something
    is missing you get one clear message listing exactly what, instead of random errors later.
  * The old MySQL migration script and the two old READMEs are not included (MySQL only).

INSTALL
  1. pip install customtkinter pyodbc
     Also install "ODBC Driver 18 for SQL Server" (free, Microsoft) if you do not have
     "ODBC Driver 17/18 for SQL Server" yet. The app picks the newest installed driver itself.
  2. SSMS -> File > Open > File -> nexus_tech_store.sql -> Execute (F5).
     !! It DROPS and recreates the database "nexus_tech_store". Back up first if it has data.
     Afterwards the Results tab shows the row counts:
        products 35, categories 14, users 2, orders 12, order_items 26,
        monitor_specs 5, monitor_accessory_specs 6
  3. Open nexus_tech_store.py and set DB_SERVER (top of the "DATABASE LAYER" section) to the
     server name you type into SSMS "Connect to Server", e.g.
        localhost\SQLEXPRESS     .\SQLEXPRESS     (localdb)\MSSQLLocalDB     localhost
     Windows Authentication is used by default. For a SQL login (e.g. "sa") set DB_USER and
     DB_PASSWORD; that login needs the SQL Server + Windows authentication mode enabled.
     (Instead of editing the file you can set the environment variables NEXUS_DB_SERVER,
      NEXUS_DB_NAME, NEXUS_DB_USER, NEXUS_DB_PASSWORD.)
  4. python nexus_tech_store.py
     Demo logins:  admin / admin123    customer / customer123

DIFFERENCES FROM MYSQL YOU MAY NOTICE IN SSMS
  * Text columns are NVARCHAR so the emoji icons are stored correctly.
  * ENUM columns became NVARCHAR + CHECK constraints (role, status, panel_type, accessory_type).
  * UNSIGNED INT became INT (+ CHECK where it mattered).
  * users.email uniqueness is a filtered unique index (SQL Server allows only one NULL in a
    plain UNIQUE constraint).
  * updated_at columns have no "ON UPDATE" in SQL Server; the app sets them in its UPDATE.
  * Checkout locks the product rows with UPDLOCK (the SQL Server version of SELECT ... FOR UPDATE)
    and reduces stock inside the same transaction. No triggers are used anywhere.

TROUBLESHOOTING
  "Cannot open database"            -> run nexus_tech_store.sql in SSMS first.
  "Login failed"                    -> wrong DB_USER/DB_PASSWORD, or your Windows account has no
                                       access to that server.
  "Named Pipes / server not found"  -> wrong DB_SERVER, SQL Server service not running, or (for
                                       Express) the SQL Server Browser service is stopped.
  "No SQL Server ODBC driver"       -> install ODBC Driver 18 for SQL Server.
