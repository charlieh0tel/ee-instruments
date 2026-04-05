#!/usr/bin/python3

import sys

from bs4 import BeautifulSoup
import requests


def get_png(hostname):
    page_url = f"http://{hostname}/InfiniiVision/ImageScreenCapture.asp?invert=false"
    page = requests.get(page_url)
    images = BeautifulSoup(page.content, features="html.parser").find_all("img")
    assert len(images) == 1
    image_src = images[0]["src"]
    image_url = f"http://{hostname}{image_src}"
    return requests.get(image_url).content


def main(argv):
    hostname = argv[1] if len(argv) >= 2 else "a-mx4054a-10545.local"
    png = get_png(hostname)
    sys.stdout.buffer.write(png)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
