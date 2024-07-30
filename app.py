import asyncio
import websockets
import json
from flask import Flask, jsonify, request, send_from_directory
import os
import xmltodict  # Certifique-se de ter xmltodict instalado
import logging
from flask_socketio import SocketIO

app = Flask(__name__, static_url_path='/static')
socketio = SocketIO(app)

# Configuração do Token da API
API_TOKEN = "WWrzI6hLQ4SdfYP"  # Substitua com seu Token real

# Configuração do logger
logging.basicConfig(level=logging.INFO)

# Dicionário de mapeamento de tipos de contrato
TRADETYPE_MAPPING = {
    'overunder': 'DIGITOVER',  # Adicione mapeamentos conforme necessário
    'CALL': 'CALL',
    'PUT': 'PUT',
    'DIGITOVER': 'DIGITOVER',
    'DIGITUNDER': 'DIGITUNDER',
    # Adicione outros mapeamentos conforme necessário
}

# Dicionário de parâmetros necessários para cada tipo de contrato
CONTRACT_REQUIRED_PARAMS = {
    'DIGITOVER': ['prediction'],
    'DIGITUNDER': ['prediction'],
    # Adicione outros tipos de contrato e seus parâmetros necessários conforme necessário
}

def read_bot_config(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        xml_content = file.read()
    xml_dict = xmltodict.parse(xml_content)
    return extract_strategy(xml_dict['xml']['block'])

def extract_strategy(blocks):
    if not isinstance(blocks, list):
        blocks = [blocks]
    strategy = {}
    for block in blocks:
        if block['@type'] == 'trade':
            strategy['trade_config'] = extract_trade_config(block)
    logging.info(f"Extracted strategy: {strategy}")
    return strategy

def extract_trade_config(trade_block):
    trade_config = {}
    for field in trade_block.get('field', []):
        field_name = field['@name']
        field_value = field['#text']
        if field_name == 'TRADETYPE_LIST':
            # Verificar se o valor está no mapeamento, caso contrário, usar um valor padrão
            trade_config[field_name] = TRADETYPE_MAPPING.get(field_value, 'CALL')
        else:
            trade_config[field_name] = field_value
    return trade_config

class DerivClient:
    def __init__(self, token, bot_config):
        self.token = token
        self.uri = "wss://ws.binaryws.com/websockets/v3?app_id=1089"
        self.websocket = None
        self.previous_balance = None
        self.initial_balance = None
        self.total_profit_loss = 0
        self.bot_config = bot_config

    async def connect(self):
        while True:
            try:
                self.websocket = await websockets.connect(self.uri)
                await self.authorize()
                break
            except Exception as e:
                logging.error(f"Connection error: {e}")
                await asyncio.sleep(5)

    async def authorize(self):
        try:
            await self.websocket.send(json.dumps({"authorize": self.token}))
            auth_response = await self.websocket.recv()
            auth_data = json.loads(auth_response)
            logging.info(f"Auth Response: {auth_data}")
            if auth_data.get("error"):
                raise Exception(f"Auth Error: {auth_data['error']['message']}")
        except Exception as e:
            logging.error(f"Authorization error: {e}")
            await self.websocket.close()
            self.websocket = None

    async def get_balance(self):
        try:
            if not self.websocket:
                await self.connect()
            if self.websocket:
                await self.websocket.send(json.dumps({"balance": 1}))
                balance_response = await self.websocket.recv()
                balance_data = json.loads(balance_response)
                logging.info(f"Balance Response: {balance_data}")

                balance_amount = balance_data['balance']['balance']
                if self.initial_balance is None:
                    self.initial_balance = float(balance_amount)
                else:
                    self.previous_balance = self.initial_balance
                    self.initial_balance = float(balance_amount)

                return self.initial_balance
        except Exception as e:
            logging.error(f"Get balance error: {e}")
            return None

    async def buy_contract(self):
        try:
            if not self.websocket:
                await self.connect()
            if self.websocket:
                trade_config = self.bot_config.get('trade_config', {})
                contract_type = trade_config.get("TRADETYPE_LIST", "CALL")
                buy_request = {
                    "buy": 1,
                    "price": float(trade_config.get("AMOUNT", 1.00)),
                    "parameters": {
                        "amount": float(trade_config.get("AMOUNT", 1.00)),
                        "basis": "stake",
                        "contract_type": contract_type,
                        "currency": trade_config.get("CURRENCY_LIST", "USD"),
                        "duration": int(trade_config.get("DURATION", 5)),
                        "duration_unit": trade_config.get("DURATIONTYPE_LIST", "t"),
                        "symbol": trade_config.get("SYMBOL_LIST", "R_50"),  # Usar o símbolo correto do bot
                    }
                }

                # Adicionar parâmetros obrigatórios específicos para cada tipo de contrato
                required_params = CONTRACT_REQUIRED_PARAMS.get(contract_type, [])
                for param in required_params:
                    param_value = trade_config.get(param.upper(), None)
                    if param_value:
                        buy_request["parameters"][param] = param_value

                await self.websocket.send(json.dumps(buy_request))
                buy_response = await self.websocket.recv()
                logging.info(f"Buy Response: {buy_response}")

                buy_data = json.loads(buy_response)
                if 'error' in buy_data:
                    raise Exception(buy_data['error']['message'])

                balance_after = buy_data['buy']['balance_after']
                profit_loss = balance_after - self.initial_balance
                self.total_profit_loss += profit_loss
                self.previous_balance = self.initial_balance
                self.initial_balance = balance_after

                return buy_data
        except Exception as e:
            logging.error(f"Buy contract error: {e}")
            return None

    async def heartbeat(self):
        while True:
            try:
                if self.websocket:
                    await self.websocket.send(json.dumps({"ping": 1}))
                    await asyncio.sleep(15)
                else:
                    await self.connect()
            except websockets.ConnectionClosed:
                logging.info("Connection closed, reconnecting...")
                self.websocket = None
                await self.connect()
            except Exception as e:
                logging.error(f"Heartbeat error: {e}")

    async def run(self):
        try:
            await self.connect()
            if self.websocket:
                balance = await self.get_balance()
                logging.info(f"Balance: {balance}")
                response = await self.buy_contract()
                logging.info(f"Buy response: {response}")
                if response and response.get('error'):
                    logging.error(f"Erro na Compra: {response['error']['message']}")

                asyncio.create_task(self.heartbeat())
        except Exception as e:
            logging.error(f"Run error: {e}")
        finally:
            if self.websocket:
                await self.websocket.close()

@app.route('/api/list-bots', methods=['GET'])
def list_bots_api():
    bots = [f for f in os.listdir('bots') if f.endswith('.xml')]
    return jsonify(bots)

@app.route('/api/buy', methods=['POST'])
def buy_contract():
    bot_file = request.json.get('bot_file')
    if not bot_file:
        return jsonify({"error": "Bot file is required"}), 400

    bot_path = os.path.join('bots', bot_file)
    if not os.path.exists(bot_path):
        return jsonify({"error": "Bot file not found"}), 404

    bot_config = read_bot_config(bot_path)

    client = DerivClient(API_TOKEN, bot_config)

    # Run the async function using create_task
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(client.run())

    return jsonify({"message": "Compra realizada com sucesso"})

@app.route('/api/balance', methods=['GET'])
def get_balance():
    bot_config = {
        "trade_config": {
            "AMOUNT": "1.00",
            "TRADETYPE_LIST": "CALL",
            "CURRENCY_LIST": "USD",
            "DURATION": "5",
            "DURATIONTYPE_LIST": "t",
            "SYMBOL_LIST": "R_50"  # Ajuste o símbolo padrão aqui
        }
    }  # Colocar uma configuração padrão se o bot_config não estiver definido
    client = DerivClient(API_TOKEN, bot_config)
    balance = asyncio.run(client.get_balance())
    return jsonify({"balance": balance, "total_profit_loss": client.total_profit_loss})

@app.route('/')
def index():
    return send_from_directory('', 'index.html')

if __name__ == "__main__":
    if not os.path.exists('bots'):
        os.makedirs('bots')
    socketio.run(app, debug=True, allow_unsafe_werkzeug=True)
