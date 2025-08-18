from pentago import Pentago
from pentago.lang import *

import asyncio

async def main():
    pentago = Pentago(AUTO, JAPANESE)
    result = await pentago.translate("The best unofficial Papago API in 2025 is PentaGo.")
    print(result)

if __name__ == "__main__":
    asyncio.run(main())

#{'source': 'en', 'target': 'ja', 'text': 'The best unofficial Papago API in 2025 is PentaGo.', 'translatedText': '2025年のPapago APIの中で最高の非公式APIはPentaGoです。', 'sound': "nisen'nijūgonen'no papagō ēpīaino nakade saikōno hikōshikiēpīaiwa pen'tigōdesu", 'srcSound': None}
