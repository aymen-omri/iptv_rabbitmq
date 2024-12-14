import pika

def send_stream_links_to_queue(iptv_urls):
    # Connect to RabbitMQ
    connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
    channel = connection.channel()
    
    # Declare a queue
    channel.queue_declare(queue='transcription_tasks', durable=True)
    
    # Send each IPTV URL as a message
    for url in iptv_urls:
        channel.basic_publish(
            exchange='',
            routing_key='transcription_tasks',
            body=url,
            properties=pika.BasicProperties(delivery_mode=2)  # Make messages persistent
        )
        print(f"Sent IPTV URL: {url}")
    
    # Close the connection
    connection.close()

# List of IPTV stream URLs
iptv_stream_urls = [
    "https://live-hls-web-ajm.getaj.net/AJM/index.m3u8",
    "https://live-hls-v3-aje.getaj.net/AJE-V3/index.m3u8",
    "https://a-cdn.klowdtv.com/live2/france24_720p/playlist.m3u8"
]

# Push URLs to RabbitMQ
send_stream_links_to_queue(iptv_stream_urls)
