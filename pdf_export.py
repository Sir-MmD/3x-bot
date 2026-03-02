import io
import tempfile

from fpdf import FPDF


def _sanitize(text: str) -> str:
    """Replace non-latin1 characters so core PDF fonts can render them."""
    return text.encode("latin-1", errors="replace").decode("latin-1")


class _PDF(FPDF):
    """FPDF subclass that adds page numbers in the footer."""

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")


def generate_account_pdf(accounts: list[dict], title: str) -> io.BytesIO:
    """Generate a PDF with account details and QR codes.

    Each account dict: {email, proxy_link, qr_image (BytesIO|None),
                        traffic, duration, sub_link}
    Returns BytesIO with .name = "accounts.pdf".
    """
    pdf = _PDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Title page header on first page
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _sanitize(title), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(5)

    for i, acc in enumerate(accounts):
        # Check if we need a new page (~130mm per account block, page usable ~267mm)
        if pdf.get_y() > 150:
            pdf.add_page()

        # Separator line between accounts (not before the first one)
        if i > 0:
            pdf.set_draw_color(180, 180, 180)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(5)

        # Account number + email
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, _sanitize(f"{i + 1}. {acc['email']}"), new_x="LMARGIN", new_y="NEXT")

        # Traffic / duration line
        pdf.set_font("Helvetica", "", 10)
        info = f"Traffic: {acc['traffic']}  |  Duration: {acc['duration']}"
        pdf.cell(0, 6, _sanitize(info), new_x="LMARGIN", new_y="NEXT")

        # Panel name if available
        if acc.get("panel"):
            pdf.cell(0, 6, _sanitize(f"Panel: {acc['panel']}"), new_x="LMARGIN", new_y="NEXT")

        # Subscription link if available
        if acc.get("sub_link"):
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(0, 6, _sanitize(f"Subscription: {acc['sub_link']}"), new_x="LMARGIN", new_y="NEXT")

        pdf.ln(2)

        # QR code + proxy link side by side
        qr_img = acc.get("qr_image")
        proxy_link = acc.get("proxy_link", "")

        if qr_img:
            qr_x = pdf.get_x()
            qr_y = pdf.get_y()
            qr_size = 60  # mm

            # Write QR image to a temp file (fpdf needs a file path)
            qr_img.seek(0)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(qr_img.read())
                tmp_path = tmp.name
            pdf.image(tmp_path, x=qr_x, y=qr_y, w=qr_size, h=qr_size)

            # Proxy link beside the QR code in monospace
            if proxy_link:
                pdf.set_xy(qr_x + qr_size + 5, qr_y)
                pdf.set_font("Courier", "", 7)
                # Wrap the proxy link text in the remaining width
                link_width = 200 - (qr_x + qr_size + 5) - 10
                pdf.multi_cell(link_width, 4, _sanitize(proxy_link))

            pdf.set_y(qr_y + qr_size + 3)
        elif proxy_link:
            # No QR, just show the link
            pdf.set_font("Courier", "", 7)
            pdf.multi_cell(0, 4, _sanitize(proxy_link))
            pdf.ln(3)

    buf = io.BytesIO()
    buf.write(pdf.output())
    buf.seek(0)
    buf.name = "accounts.pdf"
    return buf


def generate_single_account_pdf(
    email: str,
    proxy_link: str,
    qr_image: io.BytesIO | None,
    traffic: str,
    duration: str,
    sub_link: str | None = None,
) -> io.BytesIO:
    """Convenience wrapper to generate a PDF for a single account."""
    return generate_account_pdf(
        [
            {
                "email": email,
                "proxy_link": proxy_link,
                "qr_image": qr_image,
                "traffic": traffic,
                "duration": duration,
                "sub_link": sub_link,
            }
        ],
        title=f"Account: {email}",
    )
