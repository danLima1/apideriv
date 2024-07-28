import asyncio
import websockets
import os
import json

class BotLogic:
    def __init__(self, api_token):
        self.api_token = api_token
        self.app_state = {
            'stake': 1,
            'initialStake': 1,
            'MartingaleFactor': 2,
            'targetProfit': 10,
            'totalProfit': 0,
            'lowestBalance': None,
            'lowestLoss': None,
            'lastTick': None,
            'sma': None,
            'currentContractId': None,
            'smaHistory': [],
            'balance': None,
            'running': False
        }
        self.bot_symbols = {
            'bot1': 'R_100',
            'bot2': 'R_50',
            'bot3': 'R_75',
        }

    async def websocket_handler(self, websocket, path):
        print('New client connected')
        await self.start_tick_stream(websocket, 'R_100')

        async for message in websocket:
            print(f'Received message => {message}')
            message_str = message

            if message_str == 'stop':
                self.app_state['running'] = False
                await self.send_message(websocket, {'type': 'status', 'message': 'Bot stopped'})
                return

            self.app_state = self.initial_app_state()
            self.app_state['running'] = True
            await self.run_bot_logic(message_str, websocket)

        print('Client has disconnected')

    def initial_app_state(self):
        return {
            'stake': 1,
            'initialStake': 1,
            'MartingaleFactor': 2,
            'targetProfit': 10,
            'totalProfit': 0,
            'lowestBalance': None,
            'lowestLoss': None,
            'lastTick': None,
            'sma': None,
            'currentContractId': None,
            'smaHistory': [],
            'balance': None,
            'running': False
        }

    async def run_bot_logic(self, bot_name, websocket):
        xml_file_path = os.path.join(os.path.dirname(__file__), 'bots', f'{bot_name}.xml')
        try:
            with open(xml_file_path, 'r', encoding='utf-8') as file:
                xml_data = xmltodict.parse(file.read())
                self.initialize_variables(xml_data['xml']['variables']['variable'], bot_name)

                symbol = self.bot_symbols.get(bot_name, 'R_100')
                await self.start_tick_stream(websocket, symbol)

        except Exception as e:
            await self.handle_error(websocket, str(e))

    async def start_tick_stream(self, websocket, symbol):
        await self.retry_connection(websocket, symbol)

    async def retry_connection(self, websocket, symbol, retries=5, delay=2):
        for attempt in range(retries):
            try:
                async with websockets.connect('wss://ws.binaryws.com/websockets/v3?app_id=1089', timeout=10) as deriv_ws:
                    await deriv_ws.send(json.dumps({'authorize': self.api_token}))

                    async for message in deriv_ws:
                        response = json.loads(message)

                        if 'error' in response:
                            await self.handle_error(websocket, response['error']['message'])
                            return

                        if response['msg_type'] == 'authorize':
                            await deriv_ws.send(json.dumps({"ticks_history": symbol, "end": "latest", "count": 100}))
                            await deriv_ws.send(json.dumps({"balance": 1, "subscribe": 1}))

                        if response['msg_type'] == 'balance':
                            self.app_state['balance'] = response['balance']['balance']
                            await self.send_message(websocket, {'type': 'balance', 'balance': f"{self.app_state['balance']:.2f}"})

                        if response['msg_type'] == 'history':
                            self.update_app_state(response)
                            await self.send_message(websocket, {'type': 'history', 'prices': self.app_state['smaHistory']})
                            await self.start_tick_subscription(deriv_ws, websocket, symbol)

                        if response['msg_type'] == 'tick':
                            self.update_app_state(response)
                            await self.send_message(websocket, {'type': 'tick', 'tick': self.app_state['lastTick']})

                            if self.app_state['running'] and self.should_buy(self.app_state['lastTick'], self.app_state['sma']):
                                await self.request_proposal(deriv_ws, self.app_state['stake'], symbol)

                        if response['msg_type'] == 'proposal':
                            if self.app_state['running'] and self.should_buy(self.app_state['lastTick'], self.app_state['sma']):
                                await self.buy_contract(deriv_ws, response['proposal']['id'], self.app_state['stake'], websocket)

                        if response['msg_type'] == 'buy':
                            self.app_state['currentContractId'] = response['buy']['contract_id']
                            await self.send_message(websocket, {'type': 'buy', 'contract_id': self.app_state['currentContractId']})

                        if response['msg_type'] == 'sell':
                            profit = float(response['sell']['sold_for']) - self.app_state['stake']
                            self.app_state['totalProfit'] += profit
                            message = f"Contrato finalizado com {'lucro' if profit >= 0 else 'prejuízo'} de ${profit:.2f}"
                            await self.send_message(websocket, {'type': 'contract_finalizado', 'message': message, 'profit': f"{profit:.2f}", 'balance': f"{self.app_state['balance']:.2f}"})
                return  # Successful connection, exit retry loop
            except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
                print(f"Connection attempt {attempt + 1} failed. Retrying in {delay} seconds...")
                await asyncio.sleep(delay)

        await self.handle_error(websocket, "Connection attempts failed. Please try again later.")

    async def start_tick_subscription(self, deriv_ws, websocket, symbol):
        await deriv_ws.send(json.dumps({"ticks": symbol, "subscribe": 1}))

    async def request_proposal(self, deriv_ws, stake, symbol):
        amount = float(stake)
        if amount <= 0:
            print(f'Invalid stake amount: {stake}')
            return

        await deriv_ws.send(json.dumps({
            "proposal": 1,
            "amount": f"{amount:.2f}",
            "basis": "stake",
            "contract_type": "CALL",
            "currency": "USD",
            "duration": 5,
            "duration_unit": "t",
            "symbol": symbol
        }))

    def should_buy(self, last_tick, sma):
        return last_tick > sma

    async def buy_contract(self, deriv_ws, proposal_id, stake, websocket):
        amount = float(stake)
        if amount <= 0:
            print(f'Invalid stake amount: {stake}')
            await self.handle_error(websocket, f'Invalid stake amount: {stake}')
            return

        await deriv_ws.send(json.dumps({"buy": proposal_id, "price": f"{amount:.2f}"}))

    async def send_message(self, websocket, message):
        if websocket.open:
            await websocket.send(json.dumps(message))
        else:
            print("WebSocket connection is closed. Unable to send message.")

    async def handle_error(self, websocket, message):
        print(message)
        await self.send_message(websocket, {'type': 'error', 'message': message})

    def initialize_variables(self, variables, bot_name):
        id_mappings = {
            'bot1': {
                'stake': 'b.8A=Z%v|?!R]8swby2J',
                'initialStake': '[JQ:6ujo0P~5.c48sN/n',
                'MartingaleFactor': 'Qs!p}1o9ynq+8,VB=Oq.',
                'targetProfit': 'z(47tS:MB6xXj~Sa3R7j'
            },
            'bot2': {
                'stake': 'W#MDqi;8#K?,S(@3jcX}',
                'initialStake': '.#?I=EeXYD}6l!Cf.gZ6',
                'MartingaleFactor': 'S%:!W?llAvWoj1`W/LVa',
                'targetProfit': 'AwHaJ$uP6%`gBp!D-t!['
            },
        }

        mapping = id_mappings.get(bot_name)
        if not mapping:
            print(f'No variable mapping found for bots: {bot_name}')
            return

        for variable in variables:
            id = variable['@id']
            value = float(variable['@value'])
            if id == mapping['stake']:
                self.app_state['stake'] = value
            elif id == mapping['initialStake']:
                self.app_state['initialStake'] = value
            elif id == mapping['MartingaleFactor']:
                self.app_state['MartingaleFactor'] = value
            elif id == mapping['targetProfit']:
                self.app_state['targetProfit'] = value

    def update_app_state(self, response):
        if response['msg_type'] == 'history':
            prices = [float(price) for price in response['history']['prices']]
            self.app_state['smaHistory'] = self.calculate_sma(prices, 10)
        elif response['msg_type'] == 'tick':
            self.app_state['lastTick'] = float(response['tick']['quote'])
            if len(self.app_state['smaHistory']) >= 10:
                self.app_state['sma'] = self.calculate_sma([self.app_state['lastTick']] + self.app_state['smaHistory'], 10)[-1]

    def calculate_sma(self, data, period):
        if len(data) < period:
            return []

        sma = []
        sum_data = sum(data[:period])

        for i in range(period, len(data)):
            sma.append(sum_data / period)
            sum_data += data[i] - data[i - period]

        return sma
