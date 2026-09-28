import random
import string

def generate_short_code(length: int = 8):
    characters = string.ascii_letters + string.digits
    return ''.join(random.choices(characters, k=length))

def format_bytes(size: int):
    if not size or size <= 0:
        return "0 B"
    power = 1024
    n = 0
    power_labels = {0: '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size >= power and n < 4:
        size /= power
        n += 1
    if n == 0:
        return f"{int(size)} B"
    return f"{size:.2f} {power_labels[n]}B"
