def parse_all_chapters(story_url, main_soup):
    chapters = []
    seen_urls = set()

    # 1. TRANG MONGTRUYEN.COM (Vét cạn tất cả các trang ?page=)
    if "mongtruyen" in story_url:
        clean_base = story_url.split('?')[0].split('#')[0]
        
        # Thử quét liên tục từ page 1 đến khi nào không còn chương thì dừng
        page_num = 1
        max_empty_pages = 2  # Nếu 2 trang liên tiếp không thấy chương mới thì dừng hẳn
        empty_count = 0

        while page_num <= 100:  # Giới hạn tối đa 100 trang (khoảng 5000 chương)
            p_url = f"{clean_base}?page={page_num}"
            soup = main_soup if (page_num == 1 and p_url == story_url) else get_soup(p_url)
            
            if not soup:
                empty_count += 1
                if empty_count >= max_empty_pages: break
                page_num += 1
                continue

            found_on_page = 0
            # Quét tất cả thẻ a có chứa cấu trúc chương
            for a in soup.find_all("a", href=True):
                href = urljoin(p_url, a.get("href"))
                text = clean_text(a.get_text())
                
                if re.search(r"/(?:chuong|chapter|chap)[-_/]\d+", href, flags=re.I) or "chuong" in href:
                    if href not in seen_urls:
                        seen_urls.add(href)
                        chapters.append({"name": text, "url": href})
                        found_on_page += 1

            if found_on_page == 0:
                empty_count += 1
                if empty_count >= max_empty_pages: break
            else:
                empty_count = 0  # Reset lại nếu tìm thấy trang có chương

            page_num += 1
            time.sleep(0.1)

    # 2. TRANG WORDPRESS
    elif "wordpress.com" in story_url or main_soup.select_one(".entry-content"):
        content_area = main_soup.select_one(".entry-content") or main_soup.select_one(".post-content")
        if content_area:
            for a in content_area.find_all("a", href=True):
                href = urldefrag(urljoin(story_url, a.get("href")))[0]
                text = clean_text(a.get_text())
                if href and href not in seen_urls and href != urldefrag(story_url)[0]:
                    if re.search(r"(?:chương|chuong|c\d+|q\d+|\d+)", text, flags=re.I) or re.search(r"/\d{4}/\d{2}/\d{2}/", href):
                        if len(text) > 1:
                            seen_urls.add(href)
                            chapters.append({"name": text, "url": href})

    # 3. TRUYỆN FULL VÀ TRANG TƯƠNG TỰ
    else:
        current_url = urldefrag(story_url)[0].rstrip("/")
        visited_pages = set()
        while current_url and current_url not in visited_pages:
            visited_pages.add(current_url)
            soup = get_soup(current_url) if current_url != story_url else main_soup
            if not soup: break
            
            for a in soup.select("#list-chapter a, .list-chapter a"):
                href = urljoin(current_url, a.get("href", ""))
                if href and href not in seen_urls:
                    seen_urls.add(href)
                    chapters.append({"name": clean_text(a.get_text()), "url": href})
            
            next_a = soup.select_one(".pagination .next a, .pagination a[links-next]")
            current_url = urljoin(current_url, next_a.get("href")) if next_a and next_a.get("href") else None

    return chapters
