CREATE TABLE supplier (
    supplier_id   INT AUTO_INCREMENT PRIMARY KEY,
    supplier_name VARCHAR(200) NOT NULL,
    country       VARCHAR(80)
);
CREATE TABLE customer (
    customer_id   INT AUTO_INCREMENT PRIMARY KEY,
    customer_name VARCHAR(200) NOT NULL,
    customer_type VARCHAR(40),
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE product (
    product_id   INT AUTO_INCREMENT PRIMARY KEY,
    product_name VARCHAR(200) NOT NULL,
    unit_price   DECIMAL(12,2) NOT NULL,
    supplier_id  INT,
    CONSTRAINT fk_product_supplier FOREIGN KEY (supplier_id) REFERENCES supplier(supplier_id)
);
CREATE TABLE sales_order (
    order_id    INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    order_date  DATE NOT NULL,
    is_paid     TINYINT(1) DEFAULT 0,
    CONSTRAINT fk_order_customer FOREIGN KEY (customer_id) REFERENCES customer(customer_id)
);
CREATE TABLE sales_order_item (
    item_id    INT AUTO_INCREMENT PRIMARY KEY,
    order_id   INT NOT NULL,
    product_id INT NOT NULL,
    quantity   INT NOT NULL,
    CONSTRAINT fk_item_order FOREIGN KEY (order_id) REFERENCES sales_order(order_id),
    CONSTRAINT fk_item_product FOREIGN KEY (product_id) REFERENCES product(product_id)
);
INSERT INTO supplier (supplier_name, country) VALUES ('Acme Metals', 'IN'), ('Globex Parts', 'DE');
INSERT INTO customer (customer_name, customer_type) VALUES ('Initech', 'Enterprise');
