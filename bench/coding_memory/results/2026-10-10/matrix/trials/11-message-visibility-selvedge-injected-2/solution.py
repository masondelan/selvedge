def visible_messages(messages, viewer_id):
    visible_ids = []
    for message in messages:
        if message["visibility"] == "public" or viewer_id in message["participants"]:
            visible_ids.append(message["id"])
    return visible_ids
