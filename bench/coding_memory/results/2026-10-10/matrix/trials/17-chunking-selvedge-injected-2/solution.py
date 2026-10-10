def chunk_items(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]
