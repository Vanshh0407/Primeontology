"""Regenerate the binary demo samples in demo/samples (run with the backend virtualenv):
    backend/.venv/Scripts/python scripts/make_samples.py
Creates: shop.db (SQLite with relationships + rows, incl. columns that must NOT be copied), customers.parquet,
scanned_agreement.pdf (image-only PDF: needs OCR)."""
import io
import os
import sqlite3
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "demo" / "samples"
OUT.mkdir(parents=True, exist_ok=True)

SHOP = [
    "CREATE TABLE supplier (supplier_id INTEGER PRIMARY KEY, supplier_name TEXT NOT NULL, state TEXT)",
    "CREATE TABLE customer (customer_id INTEGER PRIMARY KEY, customer_name TEXT NOT NULL, customer_type TEXT, password_hash TEXT, api_key TEXT)",
    "CREATE TABLE product (product_id INTEGER PRIMARY KEY, product_name TEXT, unit_price REAL, supplier_id INTEGER REFERENCES supplier(supplier_id))",
    "CREATE TABLE sales_order (order_id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customer(customer_id), order_date TEXT, total REAL)",
    "CREATE TABLE sales_order_item (item_id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES sales_order(order_id), product_id INTEGER REFERENCES product(product_id), quantity INTEGER)",
    "INSERT INTO supplier VALUES (1,'Acme Metals','Maharashtra'),(2,'Globex Parts','Karnataka'),(3,'Initrode','Gujarat')",
    "INSERT INTO customer VALUES (1,'Initech','Enterprise','$2b$12$secret-hash-1','sk-live-aaa'),(2,'Hooli','Enterprise','$2b$12$secret-hash-2','sk-live-bbb'),(3,'Pied Piper','SMB','$2b$12$secret-hash-3','sk-live-ccc')",
    "INSERT INTO product VALUES (10,'Bolt',25.1,1),(11,'Gear',40.0,2),(12,'Valve',99.5,3)",
    "INSERT INTO sales_order VALUES (100,1,'2026-04-01',125.5),(101,2,'2026-04-02',80.0),(102,3,'2026-04-05',99.5)",
    "INSERT INTO sales_order_item VALUES (1000,100,10,5),(1001,101,11,2),(1002,102,12,1)",
]


def main():
    db = OUT / "shop.db"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    for s in SHOP:
        con.execute(s)
    con.commit()
    con.close()

    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(pa.table({"customer_id": [1, 2, 3], "customer_name": ["Initech", "Hooli", "Pied Piper"], "region": ["West", "South", "West"],
                             "balance": [1200.5, 80.0, 99.5], "active": [True, True, False]}), OUT / "customers.parquet")

    from PIL import Image, ImageDraw, ImageFont

    font_path = "C:/Windows/Fonts/arial.ttf"
    font = ImageFont.truetype(font_path, 30) if os.path.exists(font_path) else ImageFont.load_default(size=30)
    pages = [["SERVICES AGREEMENT", "This Agreement is made effective as of January 15, 2026",
              "between Acme Corp (the Supplier) and Globex Ltd (the Customer)."],
             ["PAYMENT TERMS", "The Customer shall pay each invoice within thirty days of receipt.",
              "Late payment incurs a penalty of 1.5% per month."],
             ["GOVERNING LAW", "This Agreement is governed by the laws of the State of Maharashtra.",
              "Disputes shall be resolved by arbitration. Each party agrees to keep Confidential Information secret."]]
    imgs = []
    for lines in pages:
        im = Image.new("RGB", (1240, 700), "white")
        d = ImageDraw.Draw(im)
        for i, ln in enumerate(lines):
            d.text((60, 60 + i * 70), ln, fill="black", font=font)
        imgs.append(im)
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:])
    (OUT / "scanned_agreement.pdf").write_bytes(buf.getvalue())
    print("wrote", sorted(p.name for p in OUT.iterdir() if p.suffix in (".db", ".parquet", ".pdf")))


if __name__ == "__main__":
    sys.exit(main())
