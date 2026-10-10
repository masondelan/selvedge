def retry_delays(count, base, cap):
    return [min(base * 2 ** i, cap) for i in range(count)]
