import os
import re
import requests
import logging
import time
from tqdm import tqdm
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
from time import sleep
import contextlib
import sys
import argparse

# Suppress TensorFlow and other libraries' verbose logging
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

parser = argparse.ArgumentParser(
    description='Download games with optional speed limit.'
)

parser.add_argument(
    '--speed-limit',
    type=float,
    default=None,
    help='Download speed limit in bytes per second'
)

args = parser.parse_args()

speed_limit_glob = args.speed_limit

@contextlib.contextmanager
def suppress_stdout_stderr():
    """Suppress stdout and stderr."""
    with open(os.devnull, 'w') as devnull:
        old_stdout = sys.stdout
        old_stderr = sys.stderr

        try:
            sys.stdout = devnull
            sys.stderr = devnull
            yield
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Base URL
BASE_URL = os.getenv("BASE_URL", "https://hshop.erista.me")

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

def get_chrome_options():
    """Configure Chrome options for headless browsing."""
    options = webdriver.ChromeOptions()

    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1200")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-extensions")
    options.add_argument("--remote-debugging-port=9222")

    return options

def sanitize_filename(filename):
    """Sanitize invalid filename characters."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1F]', '', filename)

def html_decode(filename):
    """Decode special HTML characters."""
    replacements = {
        '%3A': ':',
        '%2F': '/',
        '%2C': ',',
        '%5F': '_',
        '%28': '(',
        '%29': ')',
        "'": ''
    }

    for old, new in replacements.items():
        filename = filename.replace(old, new)

    return filename

def prompt_user_for_selection(items, prompt_message):
    """Prompt user to select items."""
    logging.info(prompt_message)

    for i, item in enumerate(items, start=1):
        print(f"{i}. {item.text.strip() if hasattr(item, 'text') else item[0]}")

    selections = input("Enter your selections: ")

    if selections.strip() == '*':
        return items

    selected_items = []

    for selection in selections.split(','):
        selection = selection.strip()

        if not selection.isdigit() or int(selection) not in range(1, len(items) + 1):
            logging.error(
                f"Invalid selection '{selection}', please try again."
            )
            exit(1)

        selected_items.append(items[int(selection) - 1])

    return selected_items

def get_main_categories():
    """Retrieve main categories."""
    response = requests.get(BASE_URL, headers=HEADERS, timeout=30)

    if response.status_code != 200:
        logging.error("Failed to load homepage.")
        exit(1)

    soup = BeautifulSoup(response.text, "html.parser")

    return soup.find_all("a", href=re.compile(r'^/c/'))

def get_games():
    """Retrieve and download games."""
    categories = get_main_categories()

    selected_categories = prompt_user_for_selection(
        categories,
        "Select main categories (comma separated, '*' for all):"
    )

    for selected_category in selected_categories:
        category_url = BASE_URL + selected_category['href']
        download_games_in_category(category_url)

def download_games_in_category(category_url):
    """Download games from a category."""

    response = requests.get(
        category_url,
        headers=HEADERS,
        timeout=30
    )

    if response.status_code != 200:
        logging.warning(f"Failed to load category: {category_url}")
        return

    soup_region = BeautifulSoup(response.text, "html.parser")

    # Updated selector
    subcategory_elements = soup_region.find_all(
        "a",
        href=re.compile(r'^/c/')
    )

    warning_displayed = False

    sub_categories = {}

    for element in subcategory_elements:
        href = element.get('href')

        if not href:
            continue

        # Skip self category
        if href == category_url.replace(BASE_URL, ''):
            continue

        text = element.text.strip()

        if not text:
            continue

        if len(text) < 2:
            continue

        sub_categories[text] = href

    if not sub_categories:
        logging.warning(f"No subcategories found for {category_url}")
        return

    sub_category_list = list(sub_categories.items())

    selected_sub_categories = prompt_user_for_selection(
        sub_category_list,
        f"Select subcategories for {category_url.replace(BASE_URL + '/c/', '')}:"
    )

    for sub_category_name, sub_category_link in selected_sub_categories:

        download_path = os.path.join(
            "./downloads",
            category_url.replace(BASE_URL + '/c/', ''),
            sanitize_filename(sub_category_name)
        )

        os.makedirs(download_path, exist_ok=True)

        offset = 0

        while True:

            url = BASE_URL + sub_category_link + f"?count=100&offset={offset}"

            logging.info(f"Scanning: {url}")

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30
            )

            if response.status_code != 200:
                logging.warning(f"Failed to load page: {url}")
                break

            soup_offset = BeautifulSoup(response.text, "html.parser")

            # Remove duplicates
            game_links = list(set(
                a['href']
                for a in soup_offset.find_all('a', href=True)
                if re.match(r'^/t/\d+$', a['href'])
            ))

            if not game_links:
                logging.info(f"No more content found at {url}")
                break

            for game_link in game_links:

                try:
                    game_url = BASE_URL + game_link

                    logging.info(f"Checking: {game_url}")

                    response = requests.get(
                        game_url,
                        headers=HEADERS,
                        timeout=30
                    )

                    if response.status_code != 200:
                        logging.warning(
                            f"Failed to load game page: {game_url}"
                        )
                        continue

                    soup_game = BeautifulSoup(
                        response.text,
                        "html.parser"
                    )

                    # Updated selector for new layout
                    direct_download_element = soup_game.select_one(
                        'a[href*="download"][href*="/content/"]'
                    )

                    if direct_download_element:

                        download_url = direct_download_element.get('href')

                        if download_url:

                            download_game(
                                download_url,
                                download_path,
                                speed_limit=speed_limit_glob
                            )

                        else:
                            logging.warning(
                                f"Download link missing href: {game_url}"
                            )

                    else:
                        logging.warning(
                            f"Direct download link not found: {game_url}"
                        )

                except Exception as e:
                    logging.error(
                        f"Error processing {game_link}: {e}"
                    )

            if len(game_links) < 100:
                break

            offset += 100

def download_game(url, download_path, speed_limit=None):
    """Download a single game."""

    try:

        response = requests.get(
            url,
            stream=True,
            timeout=30,
            headers=HEADERS
        )

        if response.status_code != 200:
            logging.warning(f"Failed to download from {url}")
            return

        content_disposition = response.headers.get(
            'content-disposition'
        )

        if content_disposition:

            match = re.search(
                r'filename="([^"]+)"',
                content_disposition
            )

            if match:
                filename = match.group(1)
            else:
                filename = url.split('/')[-1]

        else:
            filename = url.split('/')[-1]

        game_id = url.split('/')[-1].split('?')[0]

        filename = sanitize_filename(filename)
        filename = html_decode(filename)

        filename_parts = filename.rsplit('.', 1)

        if len(filename_parts) == 2:
            filename = (
                f"{filename_parts[0]}"
                f".[hID-{game_id}]"
                f".{filename_parts[1]}"
            )
        else:
            filename = f"{filename}.[hID-{game_id}]"

        full_final_path = os.path.join(
            download_path,
            filename
        )

        tempfilename = f"{filename}.part"

        full_temp_path = os.path.join(
            download_path,
            tempfilename
        )

        total_length = int(
            response.headers.get('content-length', 0)
        )

        if (
            os.path.exists(full_final_path)
            and os.path.getsize(full_final_path) == total_length
        ):
            logging.info(
                f"{filename} already downloaded."
            )
            return

        chunk_size = 4096

        if speed_limit:
            chunk_time = chunk_size / speed_limit

        with open(full_temp_path, 'wb') as f, tqdm(
            total=total_length,
            unit='B',
            unit_scale=True,
            unit_divisor=1024,
            desc=f"{filename} ({total_length/1024/1024:.2f} MB)"
        ) as bar:

            start_time = time.time()

            for data in response.iter_content(
                chunk_size=chunk_size
            ):

                if not data:
                    continue

                f.write(data)

                bar.update(len(data))

                if speed_limit:

                    elapsed = time.time() - start_time

                    if elapsed < chunk_time:
                        sleep(chunk_time - elapsed)

                    start_time = time.time()

        os.replace(full_temp_path, full_final_path)

        logging.info(f"Finished: {filename}")

    except requests.exceptions.RequestException as e:
        logging.error(
            f"Download error: {e}"
        )

    except Exception as e:
        logging.error(
            f"Unexpected error: {e}"
        )

if __name__ == "__main__":

    try:
        get_games()

    except KeyboardInterrupt:
        logging.info(
            "Download interrupted by user. Exiting..."
        )
        exit(0)
