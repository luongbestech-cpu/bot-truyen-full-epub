# ============================================================
# 🤖 TELEGRAM BOT → TRUYỆN FULL → EPUB
# CHẠY TRÊN GITHUB
# ============================================================

import os
import re
import time
import asyncio
import requests

from bs4 import BeautifulSoup
from ebooklib import epub

from urllib.parse import (
    urljoin,
    urldefrag,
)

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
)


# ============================================================
# 🔑 TOKEN
# Lấy từ GitHub Secrets
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()


# ============================================================
# CẤU HÌNH
# ============================================================

REQUEST_TIMEOUT = 15

MAX_PAGES = 2000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": (
        "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7"
    ),
}

session = requests.Session()
session.headers.update(HEADERS)


# ============================================================
# KIỂM TRA TOKEN
# ============================================================

if (
    not BOT_TOKEN
    or ":" not in BOT_TOKEN
):

    raise ValueError(
        "❌ BOT_TOKEN chưa được cấu hình trong GitHub Secrets."
    )


# ============================================================
# REQUEST
# ============================================================

def fetch(url, timeout=REQUEST_TIMEOUT):

    try:

        response = session.get(
            url,
            timeout=timeout,
            allow_redirects=True,
        )

        response.raise_for_status()

        return response

    except Exception as e:

        print(f"⚠️ Không tải được: {url}")
        print(f"   {type(e).__name__}: {e}")

        return None


def get_soup(url):

    response = fetch(url)

    if response is None:
        return None

    return BeautifulSoup(
        response.text,
        "lxml"
    )


# ============================================================
# URL
# ============================================================

def normalize_url(url):

    if not url:
        return ""

    url = urldefrag(url)[0]

    return url.rstrip("/")


# ============================================================
# TEXT
# ============================================================

def clean_text(text):

    return re.sub(
        r"\s+",
        " ",
        text or ""
    ).strip()


# ============================================================
# SỐ CHƯƠNG
# ============================================================
def extract_chapter_number(text):

    text = clean_text(text)

    patterns = [

        r"(?:chương|chuong|chapter|chap)"
        r"\s*(\d+)"
        r"(?:\s*[-–]\s*(\d+))?",

        r"/chuong[-_](\d+)"
        r"(?:[-_](\d+))?",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.I
        )

        if not match:
            continue

        first = int(match.group(1))

        second = match.group(2)

        if second:

            return (
                first
                + int(second) / 10
            )

        return float(first)

    return None


# ============================================================
# TÊN CHƯƠNG
# ============================================================

def get_chapter_name(a, href):

    span = a.select_one(
        ".chapter-text"
    )

    if span:

        name = clean_text(
            span.get_text(
                " ",
                strip=True
            )
        )

        if name:
            return name


    title = clean_text(
        a.get("title")
    )

    if title:
        return title


    text = clean_text(
        a.get_text(
            " ",
            strip=True
        )
    )

    if text:
        return text


    match = re.search(
        r"/chuong[-_](\d+(?:[-_]\d+)?)[^/]*",
        href,
        flags=re.I
    )

    if match:

        return (
            "Chương "
            + match.group(1)
        )


    return "Chương"


# ============================================================
# LINK CHƯƠNG
# ============================================================

def is_chapter_link(text, href):

    combined = (
        clean_text(text)
        + " "
        + (href or "")
    )

    if re.search(
        r"(?:chương|chuong|chapter|chap)"
        r"\s*\d+",
        combined,
        flags=re.I
    ):

        return True


    if re.search(
        r"/chuong[-_]\d+",
        href or "",
        flags=re.I
    ):

        return True


    return False


# ============================================================
# LẤY CHƯƠNG TRÊN PAGE
# ============================================================

def parse_chapters(
    page_url,
    soup
):

    result = {}

    if soup is None:
        return result


    containers = []

    main = soup.select_one(
        "#list-chapter"
    )

    if main:
        containers.append(main)


    if not containers:

        containers = soup.select(
            ".list-chapter"
        )


    if not containers:

        containers = [soup]


    for container in containers:

        for a in container.find_all(
            "a",
            href=True
        ):

            href = urljoin(
                page_url,
                a.get("href")
            )

            href = normalize_url(
                href
            )

            if not href:
                continue


            name = get_chapter_name(a,
                href
            )


            if not is_chapter_link(
                name,
                href
            ):

                continue


            number = extract_chapter_number(
                name
                + " "
                + href
            )


            if number is None:
                continue


            result[href] = {

                "name": name,

                "url": href,

                "number": number,

            }


    return result


# ============================================================
# SỐ PAGE
# ============================================================

def get_page_number(url):

    patterns = [

        r"/page[-/](\d+)",

        r"/trang[-_](\d+)",

        r"[?&]page=(\d+)",

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            url,
            flags=re.I
        )

        if match:

            return int(
                match.group(1)
            )


    return None


# ============================================================
# TÌM PAGE TIẾP THEO
# ============================================================

def find_pagination_links(
    page_url,
    soup
):

    pages = []

    if soup is None:
        return pages


    seen = set()


    selectors = [

        ".pagination",

        "ul.pagination",

        ".page-navigation",

        ".pager",

        "nav",

    ]


    nodes = []


    for selector in selectors:

        found = soup.select(
            selector
        )

        if found:

            nodes.extend(found)


    if not nodes:

        nodes = [soup]


    for node in nodes:

        for a in node.find_all(
            "a",
            href=True
        ):

            href = urljoin(
                page_url,
                a.get("href")
            )

            href = normalize_url(
                href
            )

            if not href:
                continue


            text = clean_text(
                a.get_text(
                    " ",
                    strip=True
                )
            ).lower()


            if is_chapter_link(
                text,
                href
            ):

                continue


            low = href.lower()

            is_page = False


            if re.search(
                r"/page[-/]?\d+",
                low
            ):

                is_page = True


            if re.search(
                r"/trang[-_]\d+",
                low
            ):

                is_page = True


            if re.search(
                r"[?&]page=\d+",
                low
            ):

                is_page = True


            if text.isdigit():

                n = int(text)

                if 1 <= n <= MAX_PAGES:

                    is_page = True


            if text in {
                "next",
                "tiếp",
                "trang sau",
                "sau","»",
                "›",
                "→",
            }:

                is_page = True


            if is_page and href not in seen:

                seen.add(href)

                pages.append(href)


    return pages


# ============================================================
# PAGE KẾ TIẾP
# ============================================================

def find_next_page(
    current_url,
    soup,
    visited
):

    links = find_pagination_links(
        current_url,
        soup
    )


    candidates = []


    for link in links:

        if link in visited:
            continue


        number = get_page_number(
            link
        )


        if number is not None:

            candidates.append(
                (
                    number,
                    link
                )
            )


    if candidates:

        candidates.sort(
            key=lambda x: x[0]
        )


        current_number = get_page_number(
            current_url
        )


        if current_number is None:

            current_number = 1


        for
