from database import IS_POSTGRES, get_connection


PRODUCTS = [
    ("Linen Ease Shirt", "Women", 2499, 2999, "https://images.unsplash.com/photo-1596755094514-f87e34085b2c?auto=format&fit=crop&w=900&q=85", "Bestseller", "Oat", "A breezy linen-blend shirt with an easy, oversized silhouette.", "https://images.unsplash.com/photo-1596755094514-f87e34085b2c?auto=format&fit=crop&w=900&q=85"),
    ("Textured Knit Polo", "Men", 2899, None, "https://images.unsplash.com/photo-1617137968427-85924c800a22?auto=format&fit=crop&w=900&q=85", "New", "Sand", "A softly structured knit polo made for dressed-down days.", "https://images.unsplash.com/photo-1617137968427-85924c800a22?auto=format&fit=crop&w=900&q=85"),
    ("Sculpted Midi Dress", "Women", 3999, 4799, "https://images.unsplash.com/photo-1595777457583-95e059d581b8?auto=format&fit=crop&w=900&q=85", "Limited", "Terracotta", "Fluid lines and thoughtful tailoring in a day-to-evening dress.", "https://images.unsplash.com/photo-1595777457583-95e059d581b8?auto=format&fit=crop&w=900&q=85"),
    ("Relaxed Pleat Trousers", "Women", 3199, None, "https://images.unsplash.com/photo-1594633312681-425c7b97ccd1?auto=format&fit=crop&w=900&q=85", None, "Stone", "High-rise trousers with soft front pleats and an elegant drape.", "https://images.unsplash.com/photo-1594633312681-425c7b97ccd1?auto=format&fit=crop&w=900&q=85"),
    ("Everyday Overshirt", "Men", 3499, 3999, "https://images.unsplash.com/photo-1603252109303-2751441dd157?auto=format&fit=crop&w=900&q=85", "Bestseller", "Olive", "A versatile mid-weight layer with clean utility details.", "https://images.unsplash.com/photo-1603252109303-2751441dd157?auto=format&fit=crop&w=900&q=85"),
    ("Soft Rib Co-ord", "Women", 3599, None, "https://images.unsplash.com/photo-1551488831-00ddcb6c6bd3?auto=format&fit=crop&w=900&q=85", "New", "Cocoa", "An effortlessly polished ribbed set designed for all-day comfort.", "https://images.unsplash.com/photo-1551488831-00ddcb6c6bd3?auto=format&fit=crop&w=900&q=85"),
    ("Classic Camp Collar", "Men", 2299, 2699, "https://images.unsplash.com/photo-1602810318383-e386cc2a3ccf?auto=format&fit=crop&w=900&q=85", None, "Ivory", "A refined warm-weather shirt cut from breathable cotton.", "https://images.unsplash.com/photo-1602810318383-e386cc2a3ccf?auto=format&fit=crop&w=900&q=85"),
    ("Drape Studio Blazer", "Women", 4499, None, "https://images.unsplash.com/photo-1591369822096-ffd140ec948f?auto=format&fit=crop&w=900&q=85", "Limited", "Camel", "Relaxed tailoring with a soft shoulder and modern proportions.", "https://images.unsplash.com/photo-1591369822096-ffd140ec948f?auto=format&fit=crop&w=900&q=85"),
]

OPTIONAL_IMAGE_URLS = [
    "https://images.unsplash.com/photo-1529139574466-a303027c1d8b?auto=format&fit=crop&w=1200&q=85",
    "https://images.unsplash.com/photo-1483985988355-763728e1935b?auto=format&fit=crop&w=1200&q=85",
    "https://images.unsplash.com/photo-1490481651871-ab68de25d43d?auto=format&fit=crop&w=1200&q=85",
    "https://images.unsplash.com/photo-1539109136881-3be0616acf4b?auto=format&fit=crop&w=1200&q=85",
    "https://images.unsplash.com/photo-1506629082955-511b1aa562c8?auto=format&fit=crop&w=1200&q=85",
]
OPTIONAL_IMAGE_FIELDS = ("image_back", "image_side", "image_closeup", "image_model", "image_fit")
PRODUCT_OPTIONAL_IMAGES = {
    product_id: dict(zip(OPTIONAL_IMAGE_FIELDS, OPTIONAL_IMAGE_URLS[offset:] + OPTIONAL_IMAGE_URLS[:offset]))
    for product_id, offset in ((product_id, (product_id - 1) % len(OPTIONAL_IMAGE_URLS)) for product_id in range(1, len(PRODUCTS) + 1))
}


def initialize_schema():
    connection = get_connection()
    cursor = connection.cursor()
    id_type = "BIGSERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    wishlist_id_type = "BIGINT" if IS_POSTGRES else "INTEGER"
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id %s,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                item_type TEXT,
                sizes TEXT,
                price INTEGER NOT NULL,
                old_price INTEGER,
                image TEXT NOT NULL,
                badge TEXT,
                color TEXT NOT NULL,
                description TEXT NOT NULL,
                image_front TEXT,
                image_back TEXT,
                image_side TEXT,
                image_closeup TEXT,
                image_model TEXT,
                image_fit TEXT
            );
        """ % id_type)

        if IS_POSTGRES:
            cursor.execute("SELECT column_name AS name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'products'")
        else:
            cursor.execute("PRAGMA table_info(products)")
        product_columns = {row["name"] for row in cursor.fetchall()}
        for name in ("item_type", "sizes"):
            if name not in product_columns:
                cursor.execute(f"ALTER TABLE products ADD COLUMN {name} TEXT")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wishlist (
                product_id %s PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE
            );
        """ % wishlist_id_type)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id %s,
                customer TEXT NOT NULL,
                email TEXT,
                mobile TEXT,
                address TEXT,
                total INTEGER NOT NULL,
                items TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'confirmed',
                razorpay_order_id TEXT,
                razorpay_payment_id TEXT,
                upi_utr TEXT
            );
        """ % id_type)
        if IS_POSTGRES:
            cursor.execute("SELECT column_name AS name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'orders'")
        else:
            cursor.execute("PRAGMA table_info(orders)")
        order_columns = {row["name"] for row in cursor.fetchall()}
        for name, declaration in (
            ("mobile", "TEXT"),
            ("address", "TEXT"),
            ("status", "TEXT NOT NULL DEFAULT 'confirmed'"),
            ("razorpay_order_id", "TEXT"),
            ("razorpay_payment_id", "TEXT"),
            ("upi_utr", "TEXT"),
        ):
            if name not in order_columns:
                cursor.execute(f"ALTER TABLE orders ADD COLUMN {name} {declaration}")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS orders_razorpay_payment_id_idx ON orders(razorpay_payment_id) WHERE razorpay_payment_id IS NOT NULL;")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS orders_upi_utr_idx ON orders(upi_utr) WHERE upi_utr IS NOT NULL;")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payment_sessions (
                razorpay_order_id TEXT PRIMARY KEY,
                customer TEXT NOT NULL,
                email TEXT,
                mobile TEXT NOT NULL,
                address TEXT NOT NULL,
                amount INTEGER NOT NULL,
                items TEXT NOT NULL,
                completed BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TEXT NOT NULL
            );
        """)
        connection.commit()

        cursor.execute("SELECT COUNT(*) AS product_count FROM products;")
        if cursor.fetchone()["product_count"] == 0:
            for product_id, product in enumerate(PRODUCTS, start=1):
                optional_images = PRODUCT_OPTIONAL_IMAGES[product_id]
                cursor.execute("""
                    INSERT INTO products
                    (name, category, price, old_price, image, badge, color, description, image_front,
                     image_back, image_side, image_closeup, image_model, image_fit)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (*product, *(optional_images[field] for field in OPTIONAL_IMAGE_FIELDS)))
            cursor.execute("UPDATE products SET image_front = image WHERE image_front IS NULL;")
            connection.commit()
    finally:
        cursor.close()
        connection.close()