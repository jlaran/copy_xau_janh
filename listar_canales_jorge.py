from telethon.sync import TelegramClient

api_id = 29083952            # Tu API ID
api_hash = '5d8e592c25a054ae62f33baabe008664'    # Tu API HASH
nombre_sesion = 'local_session'

client = TelegramClient(nombre_sesion, api_id, api_hash)
client.start()

print("🔍 Chats disponibles:\n")
for dialogo in client.iter_dialogs():
    tipo = "📢 Canal" if dialogo.is_channel else "💬 Chat/Grupo"
    print(f"{tipo} → {dialogo.name} | ID: {dialogo.id}")