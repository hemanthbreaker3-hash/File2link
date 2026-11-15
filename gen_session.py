"""Generate a wzgram session string for high-speed multi-connection streaming."""
import asyncio
import os

from dotenv import load_dotenv
from wzgram import Client

load_dotenv()


async def generate_session():
    api_id = os.getenv("API_ID")
    api_hash = os.getenv("API_HASH")
    if not api_id or not api_hash:
        print("Error: API_ID or API_HASH not found in .env")
        return

    print("WZGRAM / HITLER Session Generator")
    print("--------------------------------")
    async with Client(
        name="gen_session",
        api_id=int(api_id),
        api_hash=api_hash,
        in_memory=True,
    ) as app:
        session_str = await app.export_session_string()
        print("\nLOGIN SUCCESSFUL!")
        print("\n--- YOUR SESSION STRING (COPY THIS) ---")
        print(f"\n{session_str}\n")
        print("---------------------------------------")
        print("Paste into .env as SESSIONS=string1,string2,...")
        print("Multiple sessions = faster parallel downloads.")


if __name__ == "__main__":
    asyncio.run(generate_session())
