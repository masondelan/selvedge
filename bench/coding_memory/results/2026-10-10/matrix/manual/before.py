def visible_messages(messages, viewer_id):
    return [message["id"] for message in messages
            if viewer_id in message["participants"]]
