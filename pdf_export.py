import io
import tempfile
from pathlib import Path

from fpdf import FPDF

from db import get_user_lang
from i18n import t, is_rtl

_FONTS_DIR = Path(__file__).parent / "fonts"
_VAZIRMATN = _FONTS_DIR / "Vazirmatn-Regular.ttf"
_NOTOSANS = _FONTS_DIR / "NotoSans-Regular.ttf"

# Check uharfbuzz availability for RTL text shaping
try:
    import uharfbuzz  # noqa: F401
    _HAS_SHAPING = True
except ImportError:
    _HAS_SHAPING = False


def _sanitize(text: str) -> str:
    """Replace non-latin1 characters so core PDF fonts can render them."""
    return text.encode("latin-1", errors="replace").decode("latin-1")


class _PDF(FPDF):
    """FPDF subclass that adds page numbers in the footer."""

    def __init__(self, uid: int = 0):
        super().__init__()
        self._uid = uid
        self._use_unicode = False

    def footer(self):
        self.set_y(-15)
        if self._use_unicode:
            self.set_font("UniFont", "I" if not is_rtl(self._uid) else "", 8)
        else:
            self.set_font("Helvetica", "I", 8)
        page_text = t("pdf_page", self._uid, page=self.page_no(), total="{nb}")
        if not self._use_unicode:
            page_text = _sanitize(page_text)
        self.cell(0, 10, page_text, align="C")


def _setup_font(pdf: _PDF, uid: int):
    """Register a Unicode TTF font if needed for the user's language."""
    lang = get_user_lang(uid) or "en"
    if lang == "en":
        return  # Use built-in Helvetica for English

    if lang == "fa" and _VAZIRMATN.exists():
        font_path = str(_VAZIRMATN)
    elif _NOTOSANS.exists():
        font_path = str(_NOTOSANS)
    else:
        return  # No font available, fall back to Helvetica

    pdf.add_font("UniFont", "", font_path, uni=True)
    pdf.add_font("UniFont", "B", font_path, uni=True)
    pdf.add_font("UniFont", "I", font_path, uni=True)
    pdf._use_unicode = True

    # Enable RTL text shaping for Persian
    if lang in ("fa",) and _HAS_SHAPING:
        pdf.set_text_shaping(
            use_shaping_engine=True,
            direction="rtl",
            script="arab",
            language="fa",
        )


def generate_account_pdf(accounts: list[dict], title: str, uid: int = 0) -> io.BytesIO:
    """Generate a PDF with account details and QR codes.

    Each account dict: {email, proxy_link, qr_image (BytesIO|None),
                        traffic, duration, sub_link}
    Returns BytesIO with .name = "accounts.pdf".
    """
    pdf = _PDF(uid=uid)
    _setup_font(pdf, uid)
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)

    use_uni = pdf._use_unicode

    def _text(s: str) -> str:
        return s if use_uni else _sanitize(s)

    def _set_font(style: str = "", size: int = 10):
        if use_uni:
            pdf.set_font("UniFont", style if not is_rtl(uid) else "", size)
        else:
            pdf.set_font("Helvetica", style, size)

    # Title page header on first page
    pdf.add_page()
    _set_font("B", 16)
    pdf.cell(0, 10, _text(title), new_x="LMARGIN", new_y="NEXT", align="C")
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
        _set_font("B", 12)
        pdf.cell(0, 8, _text(f"{i + 1}. {acc['email']}"), new_x="LMARGIN", new_y="NEXT")

        # Traffic / duration line
        _set_font("", 10)
        info = t("pdf_traffic", uid, traffic=acc["traffic"]) + "  |  " + t("pdf_duration", uid, duration=acc["duration"])
        pdf.cell(0, 6, _text(info), new_x="LMARGIN", new_y="NEXT")

        # Panel name if available
        if acc.get("panel"):
            pdf.cell(0, 6, _text(t("pdf_panel", uid, panel=acc["panel"])), new_x="LMARGIN", new_y="NEXT")

        # Subscription link if available
        if acc.get("sub_link"):
            _set_font("", 9)
            pdf.cell(0, 6, _text(t("pdf_subscription", uid, link=acc["sub_link"])), new_x="LMARGIN", new_y="NEXT")

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
    uid: int = 0,
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
        title=t("pdf_account_title", uid, email=email),
        uid=uid,
    )
