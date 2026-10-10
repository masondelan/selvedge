def chunk_items(items, size):
    return [items[start:start + size] for start in range(0, len(items), size)]
