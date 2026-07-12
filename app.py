import os
import re
import pandas as pd
import requests
import phonenumbers
import validators
import dns.resolver

from flask import Flask, render_template, request, send_file
from email_validator import validate_email, EmailNotValidError
from werkzeug.utils import secure_filename


app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
OUTPUT_FOLDER = "outputs"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["OUTPUT_FOLDER"] = OUTPUT_FOLDER


# -------------------------------
# Helper Functions
# -------------------------------

def clean_text(value):
    if pd.isna(value):
        return ""

    # Excel me mobile number kabhi 9876543210.0 type aa jata hai
    if isinstance(value, float) and value.is_integer():
        value = int(value)

    return str(value).strip()


def normalize_column_name(col):
    return str(col).strip().lower().replace(" ", "_")


def check_business_name(name):
    name = clean_text(name)

    if not name:
        return 0, "Business name missing"

    fake_words = [
        "test", "dummy", "fake", "abc", "xyz",
        "unknown", "na", "n/a", "sample"
    ]

    if len(name) < 3:
        return 0, "Business name too short"

    if name.lower() in fake_words:
        return 0, "Business name looks fake"

    if any(word in name.lower() for word in fake_words):
        return 5, "Business name contains suspicious word"

    if re.fullmatch(r"[0-9]+", name):
        return 0, "Business name only contains numbers"

    return 15, "Business name looks valid"


def check_business_category(category):
    category = clean_text(category)

    if not category:
        return 0, "Business category missing"

    fake_words = [
        "test", "dummy", "fake", "unknown",
        "abc", "xyz", "na", "n/a"
    ]

    if category.lower() in fake_words:
        return 0, "Business category looks fake"

    if len(category) < 3:
        return 0, "Business category too short"

    return 10, "Business category looks valid"


def check_mobile_number(number, country_code="IN"):
    number = clean_text(number)

    if not number:
        return 0, "Mobile number missing"

    number = number.replace(" ", "").replace("-", "")

    # Excel se number 9876543210.0 aa jaye toh .0 remove
    if number.endswith(".0"):
        number = number[:-2]

    try:
        parsed_number = phonenumbers.parse(number, country_code)

        if phonenumbers.is_valid_number(parsed_number):
            return 15, "Mobile number is valid"

        if phonenumbers.is_possible_number(parsed_number):
            return 8, "Mobile number is possible but not fully valid"

        return 0, "Mobile number is invalid"

    except Exception:
        return 0, "Mobile number format invalid"


def check_email(email):
    email = clean_text(email)

    if not email:
        return 0, "Email missing"

    try:
        valid = validate_email(email, check_deliverability=False)
        email = valid.email
    except EmailNotValidError:
        return 0, "Email format invalid"

    fake_domains = [
        "test.com",
        "example.com",
        "abc.com",
        "xyz.com",
        "dummy.com",
        "fake.com"
    ]

    domain = email.split("@")[-1].lower()

    if domain in fake_domains:
        return 0, "Email domain looks fake"

    try:
        dns.resolver.resolve(domain, "MX")
        return 15, "Email domain has mail server"
    except Exception:
        return 8, "Email format valid but mail server not confirmed"


def check_description(description):
    description = clean_text(description)

    if not description:
        return 0, "Business description missing"

    fake_words = ["test", "dummy", "fake", "abc", "xyz", "sample"]

    if len(description) < 15:
        return 3, "Business description too short"

    if any(word in description.lower() for word in fake_words):
        return 2, "Business description contains suspicious word"

    return 10, "Business description looks valid"


def check_address(address):
    address = clean_text(address)

    if not address:
        return 0, "Address missing"

    fake_words = [
        "test", "dummy", "fake", "unknown",
        "abc", "xyz", "na", "n/a"
    ]

    if len(address) < 8:
        return 2, "Address too short"

    if any(word in address.lower() for word in fake_words):
        return 2, "Address contains suspicious word"

    address_keywords = [
        "road", "street", "market", "sector", "nagar", "colony",
        "jaipur", "delhi", "mumbai", "india", "near", "opposite",
        "building", "floor", "area", "city", "state", "shop",
        "plot", "main road", "industrial", "complex"
    ]

    if any(keyword in address.lower() for keyword in address_keywords):
        return 15, "Address looks valid"

    return 8, "Address present but not strongly verified"


def check_website_url(url):
    url = clean_text(url)

    if not url:
        return 0, "Website URL missing"

    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    if not validators.url(url):
        return 0, "Website URL format invalid"

    try:
        response = requests.get(
            url,
            timeout=6,
            headers={"User-Agent": "Mozilla/5.0"}
        )

        if 200 <= response.status_code < 400:
            return 20, "Website is live"

        return 8, f"Website exists but returned status {response.status_code}"

    except requests.exceptions.RequestException:
        return 5, "Website URL format valid but website not reachable"


def verify_row(row):
    score = 0
    remarks = []

    business_name = row.get("business_name", "")
    business_category = row.get("business_category", "")
    mobile_number = row.get("mobile_number", "")
    email = row.get("email", "")
    business_description = row.get("business_description", "")
    address = row.get("address", "")
    website_url = row.get("website_url", "")

    checks = [
        check_business_name(business_name),
        check_business_category(business_category),
        check_mobile_number(mobile_number),
        check_email(email),
        check_description(business_description),
        check_address(address),
        check_website_url(website_url)
    ]

    for points, remark in checks:
        score += points
        remarks.append(remark)

    if score >= 70:
        status = "REAL"
    elif score >= 45:
        status = "NEEDS_REVIEW"
    else:
        status = "FAKE"

    return score, status, " | ".join(remarks)


# -------------------------------
# Flask Routes
# -------------------------------

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":

        if "file" not in request.files:
            return render_template("index.html", error="No file uploaded")

        file = request.files["file"]

        if file.filename == "":
            return render_template("index.html", error="Please select an Excel file")

        if not file.filename.endswith((".xlsx", ".xls")):
            return render_template("index.html", error="Only Excel files are allowed")

        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(file_path)

        try:
            df = pd.read_excel(file_path)

            # Column names normalize
            df.columns = [normalize_column_name(col) for col in df.columns]

            required_columns = [
                "business_name",
                "business_category",
                "mobile_number",
                "email",
                "business_description",
                "address",
                "website_url"
            ]

            missing_columns = [col for col in required_columns if col not in df.columns]

            if missing_columns:
                return render_template(
                    "index.html",
                    error=f"Missing columns in Excel: {', '.join(missing_columns)}"
                )

            scores = []
            statuses = []
            remarks_list = []

            for _, row in df.iterrows():
                score, status, remarks = verify_row(row)
                scores.append(score)
                statuses.append(status)
                remarks_list.append(remarks)

            df["verification_score"] = scores
            df["verification_status"] = statuses
            df["verification_remarks"] = remarks_list

            real_data = df[df["verification_status"] == "REAL"]
            review_data = df[df["verification_status"] == "NEEDS_REVIEW"]
            fake_data = df[df["verification_status"] == "FAKE"]

            output_filename = "verified_output.xlsx"
            output_path = os.path.join(app.config["OUTPUT_FOLDER"], output_filename)

            with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="All_Data", index=False)
                real_data.to_excel(writer, sheet_name="Real_Data", index=False)
                review_data.to_excel(writer, sheet_name="Needs_Review", index=False)
                fake_data.to_excel(writer, sheet_name="Fake_Data", index=False)

            total_records = len(df)
            real_count = len(real_data)
            review_count = len(review_data)
            fake_count = len(fake_data)

            return render_template(
                "index.html",
                success=True,
                total_records=total_records,
                real_count=real_count,
                review_count=review_count,
                fake_count=fake_count,
                download_file=output_filename
            )

        except Exception as e:
            return render_template("index.html", error=f"Error processing file: {str(e)}")

    return render_template("index.html")


@app.route("/download/<filename>")
def download_file(filename):
    file_path = os.path.join(app.config["OUTPUT_FOLDER"], filename)
    return send_file(file_path, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True)