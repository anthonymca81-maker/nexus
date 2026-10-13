-- ============================================================
-- NEXUS TECH STORE : database for Microsoft SQL Server (SSMS)
--
-- HOW TO USE
--   1. Open SQL Server Management Studio (SSMS) and connect to your server
--      (e.g. localhost\SQLEXPRESS).
--   2. File > Open > File... > choose this script, then press Execute (F5).
--      Run the WHOLE script in one go - it contains GO separators on purpose.
--   3. Put the same server name in nexus_tech_store.py (DB_SERVER).
--
-- !! WARNING !!  PART 0 DROPS the database "nexus_tech_store" if it already
-- exists and creates it again from scratch. Back up first if it holds data.
--
-- Requires SQL Server 2016 SP1 or newer (uses CREATE OR ALTER VIEW).
--
-- PART 0  Create database
-- PART 1  Tables (users, departments, categories, products, orders, order_items)
-- PART 2  Monitors & Accessories tables (monitor_specs, monitor_accessory_specs)
-- PART 3  Seed data (categories, 35 products, demo users, demo sales)
-- PART 4  Views used by the app
-- PART 5  Quick checks
--
-- No triggers are used: stock is reduced by the app inside the checkout
-- transaction, and updated_at is set by the app's UPDATE statements.
-- ============================================================


-- ============================================================
-- PART 0: DATABASE
-- ============================================================
USE master;
GO

IF DB_ID(N'nexus_tech_store') IS NOT NULL
BEGIN
    ALTER DATABASE nexus_tech_store SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE nexus_tech_store;
END
GO

-- _SC collation = full Unicode support (emoji icons are stored in NVARCHAR columns)
CREATE DATABASE nexus_tech_store COLLATE Latin1_General_100_CI_AS_SC;
GO

USE nexus_tech_store;
GO


-- ============================================================
-- PART 1: TABLES
-- ============================================================

-- ---------- users (customers + admins) ----------
CREATE TABLE dbo.users (
    id            INT IDENTITY(1,1) NOT NULL,
    username      NVARCHAR(100) NOT NULL,                 -- what people type on the login screen
    full_name     NVARCHAR(120) NOT NULL,
    email         NVARCHAR(150) NULL,
    password_hash VARCHAR(255)  NOT NULL,
    [role]        NVARCHAR(10)  NOT NULL CONSTRAINT df_users_role DEFAULT N'customer',
    created_at    DATETIME2(0)  NOT NULL CONSTRAINT df_users_created DEFAULT SYSDATETIME(),
    CONSTRAINT pk_users          PRIMARY KEY (id),
    CONSTRAINT uq_users_username UNIQUE (username),
    CONSTRAINT chk_users_role    CHECK ([role] IN (N'admin', N'customer'))
);
GO
-- SQL Server's UNIQUE allows only ONE NULL, so uniqueness of e-mail is a filtered index
-- (same behaviour as MySQL: many users may have no e-mail, but no two may share one).
CREATE UNIQUE INDEX uq_users_email ON dbo.users (email) WHERE email IS NOT NULL;
GO

-- ---------- departments (top-level groups in the sidebar) ----------
CREATE TABLE dbo.departments (
    id   INT IDENTITY(1,1) NOT NULL,
    name NVARCHAR(80) NOT NULL,
    CONSTRAINT pk_departments      PRIMARY KEY (id),
    CONSTRAINT uq_departments_name UNIQUE (name)
);
GO

-- ---------- categories (CPUs, GPUs, Smartphones, ...) ----------
CREATE TABLE dbo.categories (
    id            INT IDENTITY(1,1) NOT NULL,
    department_id INT NOT NULL,
    name          NVARCHAR(80) NOT NULL,
    CONSTRAINT pk_categories      PRIMARY KEY (id),
    CONSTRAINT uq_categories_name UNIQUE (name),
    CONSTRAINT fk_cat_dept FOREIGN KEY (department_id) REFERENCES dbo.departments (id)
);
GO

-- ---------- products ----------
CREATE TABLE dbo.products (
    id          INT IDENTITY(1,1) NOT NULL,
    category_id INT           NOT NULL,
    name        NVARCHAR(150) NOT NULL,
    specs       NVARCHAR(255) NOT NULL,
    price       DECIMAL(10,2) NOT NULL,
    rating      DECIMAL(2,1)  NOT NULL CONSTRAINT df_prod_rating DEFAULT 4.5,
    icon        NVARCHAR(16)  NOT NULL CONSTRAINT df_prod_icon DEFAULT N'📦',   -- emoji fallback
    image_path  NVARCHAR(255) NULL,                                           -- NULL = auto by name
    stock       INT           NOT NULL CONSTRAINT df_prod_stock DEFAULT 0,
    is_active   BIT           NOT NULL CONSTRAINT df_prod_active DEFAULT 1,   -- 0 = hidden from the store
    created_at  DATETIME2(0)  NOT NULL CONSTRAINT df_prod_created DEFAULT SYSDATETIME(),
    updated_at  DATETIME2(0)  NOT NULL CONSTRAINT df_prod_updated DEFAULT SYSDATETIME(),
    CONSTRAINT pk_products      PRIMARY KEY (id),
    CONSTRAINT uq_products_name UNIQUE (name),
    CONSTRAINT chk_price  CHECK (price > 0),
    CONSTRAINT chk_rating CHECK (rating BETWEEN 0 AND 5),
    CONSTRAINT chk_stock  CHECK (stock >= 0),                                 -- blocks overselling
    CONSTRAINT fk_prod_cat FOREIGN KEY (category_id) REFERENCES dbo.categories (id)
);
GO
CREATE INDEX idx_prod_category ON dbo.products (category_id);
CREATE INDEX idx_prod_active   ON dbo.products (is_active);
GO

-- ---------- orders (one row per checkout) ----------
CREATE TABLE dbo.orders (
    id         INT IDENTITY(1,1) NOT NULL,
    user_id    INT NULL,
    total      DECIMAL(12,2) NOT NULL CONSTRAINT df_orders_total DEFAULT 0,
    status     NVARCHAR(10)  NOT NULL CONSTRAINT df_orders_status DEFAULT N'completed',
    created_at DATETIME2(0)  NOT NULL CONSTRAINT df_orders_created DEFAULT SYSDATETIME(),
    CONSTRAINT pk_orders PRIMARY KEY (id),
    CONSTRAINT chk_orders_status CHECK (status IN (N'pending', N'completed', N'cancelled')),
    CONSTRAINT fk_order_user FOREIGN KEY (user_id) REFERENCES dbo.users (id) ON DELETE SET NULL
);
GO
CREATE INDEX idx_order_created ON dbo.orders (created_at);
CREATE INDEX idx_order_status  ON dbo.orders (status);
GO

-- ---------- order_items (= your sales_log) ----------
-- product_name / category_name / unit_price are SNAPSHOTS, so sales history
-- and analytics stay correct even if a product is renamed, repriced or deleted.
CREATE TABLE dbo.order_items (
    id            INT IDENTITY(1,1) NOT NULL,
    order_id      INT NOT NULL,
    product_id    INT NULL,
    product_name  NVARCHAR(150) NOT NULL,
    category_name NVARCHAR(80)  NOT NULL,
    quantity      INT           NOT NULL,
    unit_price    DECIMAL(10,2) NOT NULL,
    CONSTRAINT pk_order_items PRIMARY KEY (id),
    CONSTRAINT chk_qty CHECK (quantity > 0),
    CONSTRAINT fk_item_order   FOREIGN KEY (order_id)   REFERENCES dbo.orders (id)   ON DELETE CASCADE,
    CONSTRAINT fk_item_product FOREIGN KEY (product_id) REFERENCES dbo.products (id) ON DELETE SET NULL
);
GO
CREATE INDEX idx_item_order    ON dbo.order_items (order_id);
CREATE INDEX idx_item_product  ON dbo.order_items (product_id);
CREATE INDEX idx_item_name     ON dbo.order_items (product_name);
CREATE INDEX idx_item_category ON dbo.order_items (category_name);
GO


-- ============================================================
-- PART 2: MONITORS & ACCESSORIES TABLES
-- Both are 1-to-1 extensions of `products` (product_id is the PK AND a foreign key),
-- so they never duplicate or replace anything in the existing tables.
-- ON DELETE CASCADE: deleting a product also removes its spec row automatically.
-- ============================================================

-- ---------- monitor_specs: details for products in the 'Monitors' category ----------
CREATE TABLE dbo.monitor_specs (
    product_id       INT NOT NULL,
    screen_size_in   DECIMAL(4,1)  NOT NULL,                       -- diagonal, inches (e.g. 27.0)
    resolution       NVARCHAR(20)  NOT NULL,                       -- e.g. 2560x1440
    panel_type       NVARCHAR(10)  NOT NULL CONSTRAINT df_mspec_panel DEFAULT N'IPS',
    refresh_rate_hz  INT           NOT NULL CONSTRAINT df_mspec_refresh DEFAULT 60,
    response_time_ms DECIMAL(5,2)  NULL,                           -- e.g. 1.00 or 0.03
    aspect_ratio     NVARCHAR(10)  NOT NULL CONSTRAINT df_mspec_ratio DEFAULT N'16:9',
    is_curved        BIT           NOT NULL CONSTRAINT df_mspec_curved DEFAULT 0,
    hdr_support      NVARCHAR(40)  NULL,
    ports            NVARCHAR(200) NULL,
    updated_at       DATETIME2(0)  NOT NULL CONSTRAINT df_mspec_updated DEFAULT SYSDATETIME(),
    CONSTRAINT pk_monitor_specs PRIMARY KEY (product_id),
    CONSTRAINT fk_mspec_product FOREIGN KEY (product_id) REFERENCES dbo.products (id) ON DELETE CASCADE,
    CONSTRAINT chk_mspec_panel   CHECK (panel_type IN (N'IPS', N'VA', N'TN', N'OLED', N'QD-OLED', N'Mini-LED', N'Other')),
    CONSTRAINT chk_mspec_size    CHECK (screen_size_in > 0),
    CONSTRAINT chk_mspec_refresh CHECK (refresh_rate_hz > 0),
    CONSTRAINT chk_mspec_resp    CHECK (response_time_ms IS NULL OR response_time_ms > 0),
    -- digits 'x' digits, e.g. 2560x1440 (T-SQL has no REGEXP, so LIKE patterns are used)
    CONSTRAINT chk_mspec_res     CHECK (resolution LIKE N'[0-9]%x[0-9]%'
                                        AND resolution NOT LIKE N'%[^0-9x]%'
                                        AND resolution NOT LIKE N'%x%x%')
);
GO
CREATE INDEX idx_mspec_refresh ON dbo.monitor_specs (refresh_rate_hz);
CREATE INDEX idx_mspec_panel   ON dbo.monitor_specs (panel_type);
GO

-- ---------- monitor_accessory_specs: details for arms, cables, webcams, lights ----------
CREATE TABLE dbo.monitor_accessory_specs (
    product_id     INT NOT NULL,
    accessory_type NVARCHAR(10)  NOT NULL CONSTRAINT df_aspec_type DEFAULT N'other',
    compatibility  NVARCHAR(200) NULL,                             -- what it works with
    connector_type NVARCHAR(60)  NULL,                             -- e.g. DisplayPort 1.4 (M/M)
    length_m       DECIMAL(5,2)  NULL,                             -- cables
    max_load_kg    DECIMAL(5,2)  NULL,                             -- arms / stands
    vesa_pattern   NVARCHAR(40)  NULL,                             -- e.g. 75x75, 100x100
    updated_at     DATETIME2(0)  NOT NULL CONSTRAINT df_aspec_updated DEFAULT SYSDATETIME(),
    CONSTRAINT pk_monitor_accessory_specs PRIMARY KEY (product_id),
    CONSTRAINT fk_aspec_product FOREIGN KEY (product_id) REFERENCES dbo.products (id) ON DELETE CASCADE,
    CONSTRAINT chk_aspec_type CHECK (accessory_type IN (N'arm', N'stand', N'cable', N'adapter', N'webcam', N'light_bar', N'other')),
    CONSTRAINT chk_aspec_len  CHECK (length_m IS NULL OR length_m > 0),
    CONSTRAINT chk_aspec_load CHECK (max_load_kg IS NULL OR max_load_kg > 0)
);
GO
CREATE INDEX idx_aspec_type ON dbo.monitor_accessory_specs (accessory_type);
GO


-- ============================================================
-- PART 3: SEED DATA
-- ============================================================

-- ---------- departments & categories ----------
SET IDENTITY_INSERT dbo.departments ON;
INSERT INTO dbo.departments (id, name) VALUES
    (1, N'PC & Components'),
    (2, N'Mobile & Accessories');
SET IDENTITY_INSERT dbo.departments OFF;

SET IDENTITY_INSERT dbo.categories ON;
INSERT INTO dbo.categories (id, department_id, name) VALUES
    (1, 1, N'CPUs'),
    (2, 1, N'GPUs'),
    (3, 1, N'Motherboards'),
    (4, 1, N'RAM'),
    (5, 1, N'Prebuilt PCs'),
    (6, 1, N'Laptops'),
    (7, 2, N'Smartphones'),
    (8, 2, N'Chargers'),
    (9, 2, N'Cases'),
    (10, 2, N'Audio');
SET IDENTITY_INSERT dbo.categories OFF;

-- ---------- products (real devices, USD) ----------
SET IDENTITY_INSERT dbo.products ON;
INSERT INTO dbo.products (id, category_id, name, specs, price, rating, icon, stock) VALUES
    (1,  2,  N'NVIDIA GeForce RTX 5090 Founders Edition', N'32GB GDDR7 • 575W • 4K/8K Gaming',                     1999.00, 4.8, N'🎮', 5),
    (2,  2,  N'NVIDIA GeForce RTX 5080 Founders Edition', N'16GB GDDR7 • 360W • 4K Gaming',                        999.00,  4.7, N'🎮', 12),
    (3,  1,  N'AMD Ryzen 9 9950X3D',                      N'16 Cores • 32 Threads • 5.7GHz • 3D V-Cache',         699.00,  4.8, N'🧠', 20),
    (4,  1,  N'Intel Core Ultra 9 285K',                  N'24 Cores • 5.7GHz • LGA1851',                         589.00,  4.4, N'🧠', 20),
    (5,  3,  N'ASUS ROG Crosshair X870E Hero',            N'AM5 • DDR5 • PCIe 5.0 • Wi-Fi 7',                     699.99,  4.7, N'🔧', 10),
    (6,  3,  N'MSI MAG Z890 Tomahawk WiFi',               N'LGA1851 • DDR5 • Wi-Fi 7',                            299.99,  4.6, N'🔧', 15),
    (7,  4,  N'Corsair Vengeance RGB 32GB DDR5-6000',     N'32GB (2×16GB) • 6000MT/s • CL36',                     370.00,  4.6, N'💾', 25),
    (8,  4,  N'Corsair Vengeance 64GB DDR5-5200',         N'64GB (2×32GB) • 5200MT/s • CL40',                     690.00,  4.5, N'💾', 15),
    (9,  5,  N'Alienware Area-51 (RTX 5090)',             N'RTX 5090 • Ryzen 9 9950X3D • 32GB • 2TB',             5299.99, 4.7, N'🖥️', 4),
    (10, 5,  N'Alienware Area-51 (Base Config)',          N'RTX 50-Series • Ryzen X3D • 1500W PSU • Liquid Cooled', 3849.00, 4.6, N'🖥️', 6),
    (11, 6,  N'Apple MacBook Pro 14-inch (M5)',           N'14" Liquid Retina XDR • M5 • 16GB • 512GB',           1599.00, 4.8, N'💻', 15),
    (12, 6,  N'ASUS ROG Zephyrus G16 (2026)',             N'16" OLED 240Hz • Core Ultra 9 386H • RTX 50-Series',  2999.99, 4.6, N'💻', 8),
    (13, 7,  N'Apple iPhone 17 Pro Max',                  N'6.9" OLED • A19 Pro • 256GB • 48MP Camera System',    1199.00, 4.8, N'📱', 30),
    (14, 7,  N'Samsung Galaxy S26 Ultra',                 N'6.9" AMOLED • Snapdragon 8 Elite Gen 5 • 256GB',      1299.99, 4.7, N'📱', 30),
    (15, 7,  N'Google Pixel 10 Pro',                      N'6.3" OLED • Tensor G5 • 256GB • AI Camera',           999.00,  4.6, N'📱', 25),
    (16, 8,  N'Anker 735 Charger (65W)',                  N'65W GaN • 3 Ports • Fast Charging',                   49.99,   4.7, N'🔌', 60),
    (17, 8,  N'Apple 35W Dual USB-C Power Adapter',       N'35W • 2×USB-C • Compact',                             59.00,   4.6, N'⚡', 50),
    (18, 8,  N'Anker MagGo Power Bank (10K)',             N'10,000mAh • Qi2 Magnetic • 15W Wireless',             59.99,   4.5, N'🔋', 40),
    (19, 9,  N'Apple iPhone 17 Pro Max Silicone Case',    N'MagSafe • Silky Silicone • Microfiber Lining',        49.00,   4.5, N'🛡️', 100),
    (20, 9,  N'Spigen Tough Armor MagFit',                N'Dual-Layer • Kickstand • MagSafe Compatible',         29.99,   4.7, N'📦', 90),
    (21, 9,  N'OtterBox Symmetry Clear (MagSafe)',        N'Clear • Drop Protection • MagSafe',                   54.95,   4.6, N'📱', 70),
    (22, 10, N'Apple AirPods Pro 3',                      N'Active Noise Cancellation • Heart Rate Sensing • USB-C', 249.00, 4.8, N'🎧', 55),
    (23, 10, N'Sony WH-1000XM6',                          N'Over-Ear • Industry-Leading ANC • 30h Battery',       449.99,  4.8, N'🎵', 30),
    (24, 10, N'Bose QuietComfort Ultra Earbuds (2nd Gen)', N'ANC • Spatial Audio • Wireless',                     299.00,  4.6, N'🔊', 40);
SET IDENTITY_INSERT dbo.products OFF;

-- ---------- demo users ----------
-- DEMO ONLY: passwords are SHA-256 hashes (lower-case hex) - identical to what the Python app
-- produces with hashlib.sha256, so the login works out of the box.
--   admin    / admin123
--   customer / customer123
SET IDENTITY_INSERT dbo.users ON;
INSERT INTO dbo.users (id, username, full_name, email, password_hash, [role]) VALUES
    (1, N'admin',    N'Store Administrator', N'admin@nexus.local',
        LOWER(CONVERT(VARCHAR(64), HASHBYTES('SHA2_256', 'admin123'), 2)),    N'admin'),
    (2, N'customer', N'Demo Customer',       N'customer@nexus.local',
        LOWER(CONVERT(VARCHAR(64), HASHBYTES('SHA2_256', 'customer123'), 2)), N'customer');
SET IDENTITY_INSERT dbo.users OFF;

-- ---------- demo sales so the analytics are not empty ----------
-- (inserted directly, so demo sales do not change stock)
SET IDENTITY_INSERT dbo.orders ON;
INSERT INTO dbo.orders (id, user_id, status, created_at) VALUES
    (1,  2, N'completed', DATEADD(MINUTE, -10, SYSDATETIME())),
    (2,  2, N'completed', DATEADD(MINUTE, -25, SYSDATETIME())),
    (3,  2, N'completed', DATEADD(MINUTE, -40, SYSDATETIME())),
    (4,  2, N'completed', DATEADD(MINUTE, -55, SYSDATETIME())),
    (5,  2, N'completed', DATEADD(DAY, -1, SYSDATETIME())),
    (6,  2, N'completed', DATEADD(DAY, -1, SYSDATETIME())),
    (7,  2, N'completed', DATEADD(DAY, -2, SYSDATETIME())),
    (8,  2, N'completed', DATEADD(DAY, -2, SYSDATETIME())),
    (9,  2, N'completed', DATEADD(DAY, -3, SYSDATETIME())),
    (10, 2, N'completed', DATEADD(DAY, -4, SYSDATETIME())),
    (11, 2, N'completed', DATEADD(DAY, -5, SYSDATETIME())),
    (12, 2, N'completed', DATEADD(DAY, -6, SYSDATETIME()));
SET IDENTITY_INSERT dbo.orders OFF;

CREATE TABLE #seed_lines (order_id INT, product_id INT, qty INT);
INSERT INTO #seed_lines (order_id, product_id, qty) VALUES
    (1, 13, 1), (1, 20, 2),
    (2, 22, 2), (2, 16, 1),
    (3, 13, 1), (3, 23, 1),
    (4, 20, 3), (4, 18, 1),
    (5, 22, 1), (5, 14, 1), (5, 19, 2),
    (6, 13, 2), (6, 7, 1),
    (7, 2, 1),  (7, 22, 1),
    (8, 20, 2), (8, 16, 2),
    (9, 3, 1),  (9, 8, 1),  (9, 13, 1),
    (10, 23, 1), (10, 15, 1),
    (11, 11, 1), (11, 22, 2),
    (12, 1, 1),  (12, 20, 1);

INSERT INTO dbo.order_items (order_id, product_id, product_name, category_name, quantity, unit_price)
SELECT s.order_id, p.id, p.name, c.name, s.qty, p.price
FROM #seed_lines s
JOIN dbo.products   p ON p.id = s.product_id
JOIN dbo.categories c ON c.id = p.category_id;

DROP TABLE #seed_lines;

UPDATE o
SET o.total = ISNULL((SELECT SUM(oi.quantity * oi.unit_price)
                      FROM dbo.order_items oi WHERE oi.order_id = o.id), 0)
FROM dbo.orders o;
GO


-- ============================================================
-- MONITORS & ACCESSORIES : department, categories, sample products, spec rows
-- Every insert checks for an existing row by NAME first, so this part is safe to re-run.
-- (Sample products/prices are demo data - edit them freely in the Admin panel.)
-- ============================================================

INSERT INTO dbo.departments (name)
SELECT N'Monitors & Accessories'
WHERE NOT EXISTS (SELECT 1 FROM dbo.departments WHERE name = N'Monitors & Accessories');

INSERT INTO dbo.categories (department_id, name)
SELECT d.id, v.name
FROM dbo.departments d
CROSS JOIN (VALUES (N'Monitors'), (N'Monitor Arms'), (N'Display Cables'), (N'Webcams & Lights')) AS v(name)
WHERE d.name = N'Monitors & Accessories'
  AND NOT EXISTS (SELECT 1 FROM dbo.categories c WHERE c.name = v.name);

INSERT INTO dbo.products (category_id, name, specs, price, rating, icon, stock)
SELECT c.id, v.name, v.specs, v.price, v.rating, v.icon, v.stock
FROM (VALUES
    (N'Monitors', N'LG UltraGear 27GP850-B', N'27" • 2560x1440 • IPS • 165Hz • 1ms • HDR10', 299.99, 4.6, N'🖥️', 14),
    (N'Monitors', N'Dell UltraSharp U2723QE',       N'27" • 3840x2160 • IPS • 60Hz • 5ms • USB-C 90W', 579.99, 4.6, N'🖥️', 10),
    (N'Monitors', N'Samsung Odyssey G9 G95C 49"',   N'49" • 5120x1440 • VA • 240Hz • 1ms • Curved • HDR10+', 999.99, 4.5, N'🖥️', 6),
    (N'Monitors', N'ASUS ROG Swift OLED PG27AQDM',  N'26.5" • 2560x1440 • OLED • 240Hz • 0.03ms • HDR10', 899.99, 4.7, N'🖥️', 8),
    (N'Monitors', N'AOC 24G2SP',                    N'23.8" • 1920x1080 • IPS • 165Hz • 1ms', 139.99, 4.6, N'🖥️', 25),
    (N'Monitor Arms', N'Ergotron LX Desk Monitor Arm', N'Single Arm • Up to 11.3kg • VESA 75/100 • Height Adjustable', 199.99, 4.8, N'🔩', 20),
    (N'Monitor Arms', N'VIVO Dual Monitor Desk Mount', N'Dual Arm • 13"-27" • 10kg per Arm • VESA 75/100', 49.99, 4.5, N'🔩', 35),
    (N'Display Cables', N'Cable Matters DisplayPort 1.4 Cable (6 ft)', N'DisplayPort 1.4 • VESA Certified • 8K/60Hz • 1.8m', 14.99, 4.7, N'🔗', 80),
    (N'Display Cables', N'Belkin Ultra HD High Speed HDMI 2.1 Cable (6.6 ft)', N'HDMI 2.1 • 48Gbps • 8K/60Hz • 4K/120Hz • 2m', 29.99, 4.6, N'🔗', 60),
    (N'Webcams & Lights', N'Logitech C920s HD Pro Webcam', N'1080p/30fps • Dual Mics • Privacy Shutter • USB', 69.99, 4.6, N'📷', 45),
    (N'Webcams & Lights', N'BenQ ScreenBar Halo Monitor Light', N'Auto-Dimming • Asymmetric Light • USB-powered • Clamp Mount', 179.00, 4.7, N'💡', 18)
) AS v(cat, name, specs, price, rating, icon, stock)
JOIN dbo.categories c ON c.name = v.cat
WHERE NOT EXISTS (SELECT 1 FROM dbo.products p WHERE p.name = v.name);

INSERT INTO dbo.monitor_specs
    (product_id, screen_size_in, resolution, panel_type, refresh_rate_hz,
     response_time_ms, aspect_ratio, is_curved, hdr_support, ports)
SELECT p.id, v.sz, v.res, v.panel, v.hz, v.resp, v.ratio, v.curved, v.hdr, v.ports
FROM (VALUES
    (N'LG UltraGear 27GP850-B', 27.0, N'2560x1440', N'IPS', 165, 1.00, N'16:9', 0, N'HDR10', N'2x HDMI 2.0, DisplayPort 1.4, USB hub'),
    (N'Dell UltraSharp U2723QE', 27.0, N'3840x2160', N'IPS', 60, 5.00, N'16:9', 0, N'DisplayHDR 400', N'HDMI, DisplayPort in/out, USB-C 90W PD, RJ45, USB hub'),
    (N'Samsung Odyssey G9 G95C 49"', 49.0, N'5120x1440', N'VA', 240, 1.00, N'32:9', 1, N'HDR10+', N'HDMI 2.1, DisplayPort 1.4, USB hub'),
    (N'ASUS ROG Swift OLED PG27AQDM', 26.5, N'2560x1440', N'OLED', 240, 0.03, N'16:9', 0, N'HDR10', N'DisplayPort 1.4, 2x HDMI 2.0, USB hub'),
    (N'AOC 24G2SP', 23.8, N'1920x1080', N'IPS', 165, 1.00, N'16:9', 0, NULL, N'2x HDMI 2.0, DisplayPort')
) AS v(pname, sz, res, panel, hz, resp, ratio, curved, hdr, ports)
JOIN dbo.products p ON p.name = v.pname
WHERE NOT EXISTS (SELECT 1 FROM dbo.monitor_specs m WHERE m.product_id = p.id);

INSERT INTO dbo.monitor_accessory_specs
    (product_id, accessory_type, compatibility, connector_type, length_m, max_load_kg, vesa_pattern)
SELECT p.id, v.atype, v.compat, v.conn, v.len_m, v.load_kg, v.vesa
FROM (VALUES
    (N'Ergotron LX Desk Monitor Arm', N'arm', N'Single monitors up to 34 inch', N'Desk clamp / grommet', NULL, 11.30, N'75x75, 100x100'),
    (N'VIVO Dual Monitor Desk Mount', N'arm', N'Two 13-27 inch monitors', N'Desk clamp / grommet', NULL, 10.00, N'75x75, 100x100'),
    (N'Cable Matters DisplayPort 1.4 Cable (6 ft)', N'cable', N'DisplayPort 1.4 monitors, GPUs and docks', N'DisplayPort 1.4 (M/M)', 1.83, NULL, NULL),
    (N'Belkin Ultra HD High Speed HDMI 2.1 Cable (6.6 ft)', N'cable', N'HDMI 2.1 monitors, consoles and GPUs', N'HDMI 2.1 (M/M)', 2.00, NULL, NULL),
    (N'Logitech C920s HD Pro Webcam', N'webcam', N'Windows, macOS, ChromeOS; clips onto monitors', N'USB-A', NULL, NULL, NULL),
    (N'BenQ ScreenBar Halo Monitor Light', N'light_bar', N'Clamps onto flat and curved monitors', N'USB-A (5V)', NULL, NULL, NULL)
) AS v(pname, atype, compat, conn, len_m, load_kg, vesa)
JOIN dbo.products p ON p.name = v.pname
WHERE NOT EXISTS (SELECT 1 FROM dbo.monitor_accessory_specs a WHERE a.product_id = p.id);
GO


-- ============================================================
-- PART 4: VIEWS  (CREATE OR ALTER = safe to re-run)
-- ============================================================

-- One flat view of every completed sale line (used by all analytics queries).
CREATE OR ALTER VIEW dbo.v_sales AS
SELECT oi.id            AS line_id,
       o.id             AS order_id,
       o.created_at     AS sold_at,
       oi.product_id,
       oi.product_name,
       oi.category_name,
       oi.quantity,
       oi.unit_price,
       oi.quantity * oi.unit_price AS line_total
FROM dbo.order_items oi
JOIN dbo.orders o ON o.id = oi.order_id
WHERE o.status = N'completed';
GO

-- Store catalogue with department + category names (for the product grid).
CREATE OR ALTER VIEW dbo.v_catalog AS
SELECT p.id, p.name, p.specs, p.price, p.rating, p.icon, p.image_path, p.stock,
       c.id   AS category_id,
       c.name AS category,
       d.name AS department
FROM dbo.products p
JOIN dbo.categories  c ON c.id = p.category_id
JOIN dbo.departments d ON d.id = c.department_id
WHERE p.is_active = 1;
GO

-- Ready-made top-5 lists for the analytics panels (whole store).
CREATE OR ALTER VIEW dbo.v_top_sellers_today AS
SELECT TOP (5) product_name, category_name, SUM(quantity) AS units_sold, SUM(line_total) AS revenue
FROM dbo.v_sales
WHERE sold_at >= CAST(SYSDATETIME() AS DATE)
GROUP BY product_name, category_name
ORDER BY SUM(quantity) DESC;
GO

CREATE OR ALTER VIEW dbo.v_top_sellers_week AS
SELECT TOP (5) product_name, category_name, SUM(quantity) AS units_sold, SUM(line_total) AS revenue
FROM dbo.v_sales
WHERE sold_at >= DATEADD(DAY, -7, SYSDATETIME())
GROUP BY product_name, category_name
ORDER BY SUM(quantity) DESC;
GO

CREATE OR ALTER VIEW dbo.v_monitor_catalog AS
SELECT p.id AS product_id, p.name, p.price, p.rating, p.stock, p.icon, p.image_path,
       c.name AS category,
       m.screen_size_in, m.resolution, m.panel_type, m.refresh_rate_hz,
       m.response_time_ms, m.aspect_ratio, m.is_curved, m.hdr_support, m.ports
FROM dbo.products p
JOIN dbo.categories    c ON c.id = p.category_id
JOIN dbo.monitor_specs m ON m.product_id = p.id
WHERE p.is_active = 1;
GO

CREATE OR ALTER VIEW dbo.v_accessory_catalog AS
SELECT p.id AS product_id, p.name, p.price, p.rating, p.stock, p.icon, p.image_path,
       c.name AS category,
       a.accessory_type, a.compatibility, a.connector_type,
       a.length_m, a.max_load_kg, a.vesa_pattern
FROM dbo.products p
JOIN dbo.categories              c ON c.id = p.category_id
JOIN dbo.monitor_accessory_specs a ON a.product_id = p.id
WHERE p.is_active = 1;
GO


-- ============================================================
-- PART 5: QUICK CHECKS (results appear in the SSMS "Results" tab)
-- Expected: products 35, categories 14, users 2, orders 12, order_items 26,
--           monitor_specs 5, monitor_accessory_specs 6
-- ============================================================
SELECT 'products' AS tbl, COUNT(*) AS [rows] FROM dbo.products
UNION ALL SELECT 'categories',               COUNT(*) FROM dbo.categories
UNION ALL SELECT 'users',                    COUNT(*) FROM dbo.users
UNION ALL SELECT 'orders',                   COUNT(*) FROM dbo.orders
UNION ALL SELECT 'order_items',              COUNT(*) FROM dbo.order_items
UNION ALL SELECT 'monitor_specs',            COUNT(*) FROM dbo.monitor_specs
UNION ALL SELECT 'monitor_accessory_specs',  COUNT(*) FROM dbo.monitor_accessory_specs;

SELECT * FROM dbo.v_top_sellers_week;
GO
