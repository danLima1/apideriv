import json
import uvicorn
import xmltodict
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pathlib import Path
from pydantic import BaseModel
import logging
import os

# Carrega as variáveis de ambiente do arquivo .env
load_dotenv()

# Obtém o token da API e a porta das variáveis de ambiente
API_TOKEN = os.getenv("API_TOKEN")
PORT = int(os.getenv("PORT", 3001))

# Variável global para armazenar a conexão WebSocket
deriv_ws = None

# Verifica se o token da API foi definido
if not API_TOKEN:
    raise ValueError("API_TOKEN não está definida nas variáveis de ambiente. "
                     "Crie um arquivo .env e defina a variável API_TOKEN.")

# Cria a aplicação FastAPI
app = FastAPI()

# Adiciona o middleware CORS para permitir solicitações de qualquer origem
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Define o estado inicial da aplicação
initialAppState = {
    "stake": 1,
    "initialStake": None,
    "MartingaleFactor": 2.5,
    "targetProfit": None,
    "stopLoss": None,
    "totalProfit": 0,
    "lowestBalance": None,
    "lowestLoss": None,
    "lastTick": None,
    "currentContractId": None,
    "balance": None,
    "previousBalance": None,
    "initialBalance": None,
    "running": False,
    "botName": None,
    "trade_config": {},
    "NextTradeCondition": "Even",
    "stop_operations": False,  # Adicionado para controlar operações de compra
    "tick_subscription_id": None  # Adicionado para armazenar o ID de inscrição dos ticks
}
appState = initialAppState.copy()

botSymbols = {
    "bot1": "R_50",  # Adapte para o símbolo correto se necessário
    # ... outros bots
}


class StakeConfig(BaseModel):
    stake: float


class StopLossConfig(BaseModel):
    stopLoss: float


class TargetProfitConfig(BaseModel):
    targetProfit: float


@app.on_event("startup")
async def startup_event():
    logging.basicConfig(level=logging.INFO)
    logging.info("API started")


@app.get("/api/bots")
async def get_bots():
    bot_directory = Path("bots")
    if not bot_directory.exists():
        raise HTTPException(status_code=500, detail="Bot directory not found.")
    bot_files = [bot.stem for bot in bot_directory.glob("*.xml")]
    return bot_files


@app.post("/api/config/stake")
async def set_stake(config: StakeConfig):
    if config.stake <= 0:
        raise HTTPException(status_code=400, detail="Invalid stake value.")
    appState["stake"] = config.stake
    appState["initialStake"] = config.stake
    logging.info(f"Stake updated to {appState['stake']}")
    return {"message": "Stake updated", "stake": appState["stake"]}


@app.post("/api/config/stopLoss")
async def set_stop_loss(config: StopLossConfig):
    if config.stopLoss <= 0:
        raise HTTPException(status_code=400,
                            detail="Invalid stop loss value.")
    appState["stopLoss"] = config.stopLoss
    logging.info(f"Stop loss updated to {appState['stopLoss']}")
    return {"message": "Stop loss updated",
            "stopLoss": appState["stopLoss"]}


@app.post("/api/config/targetProfit")
async def set_target_profit(config: TargetProfitConfig):
    if config.targetProfit <= 0:
        raise HTTPException(status_code=400,
                            detail="Invalid target profit value.")
    appState["targetProfit"] = config.targetProfit
    logging.info(f"Target profit updated to {appState['targetProfit']}")
    return {"message": "Target profit updated",
            "targetProfit": appState["targetProfit"]}


# Função para fechar a conexão WebSocket
async def close_websocket_connection():
    global deriv_ws
    if deriv_ws and deriv_ws.open:
        logging.info("Closing existing WebSocket connection...")
        await deriv_ws.close()
        deriv_ws = None


# Função para desinscrever dos ticks do bot anterior
async def unsubscribe_ticks():
    global deriv_ws
    if deriv_ws and appState.get("tick_subscription_id"):
        await deriv_ws.send(json.dumps({
            "forget": appState["tick_subscription_id"]
        }))
        appState["tick_subscription_id"] = None
        logging.info("Unsubscribed from previous tick updates")


# Rota para sinalizar o término do bot
@app.post("/api/stop")
async def api_stop_bot():
    appState["running"] = False
    appState["stop_operations"] = True  # Adicionado para parar operações de compra
    logging.info("Stopping bot")
    return {"message": "Bot stopped but still updating tick values"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        async for message in websocket.iter_text():
            data = json.loads(message)
            command = data.get("command")
            if command == "start":
                bot_name = data.get("botName")
                logging.info(f"Starting bot: {bot_name}")
                appState["stop_operations"] = False  # Reseta o estado de operações ao iniciar
                await start_bot_logic(websocket, bot_name)
            elif command == "stop":
                appState["running"] = False
                appState["stop_operations"] = True  # Adicionado para parar operações de compra
                logging.info("Stopping bot")
                await websocket.send_text(
                    json.dumps({"type": "status", "message": "Bot stopped"}))
    except websockets.ConnectionClosedError as e:
        logging.error(f"WebSocket connection closed: {str(e)}")
    except Exception as e:
        logging.error(f"Error during WebSocket connection: {str(e)}")


async def start_bot_logic(websocket: WebSocket, bot_name: str):
    global deriv_ws

    appState["botName"] = bot_name
    xml_file_path = Path(f"bots/{bot_name}.xml")
    if not xml_file_path.exists():
        await websocket.send_text(
            json.dumps({"type": "error", "message": "Bot XML file not found"}))
        logging.error(f"Bot XML file not found: {bot_name}")
        return

    try:
        with open(xml_file_path, "r", encoding="utf-8") as xml_file:
            xml_content = xmltodict.parse(xml_file.read())
            initialize_variables(
                xml_content["xml"]["variables"]["variable"], bot_name)
            appState["strategy"] = extract_strategy(
                xml_content["xml"]["block"])
            appState["trade_config"] = appState["strategy"].get(
                'trade_config', {})
    except Exception as e:
        await websocket.send_text(
            json.dumps({"type": "error", "message": str(e)}))
        logging.error(f"Error reading bot XML file: {str(e)}")
        return

    await close_websocket_connection()  # Fecha a conexão WebSocket existente
    await run_bot_logic(websocket)  # Inicia a lógica do bot com uma nova conexão WebSocket


def initialize_variables(variables, bot_name):
    variable_mappings = {
        "stake": "gsQah}04l(VNTYJ`*;{Y",
        "initialStake": "gsQah}04l(VNTYJ`*;{Y",
        "MartingaleFactor": "id_da_variável_martingale",
        "targetProfit": "oQ~TQa?Dq78]MM},5;*l",
        "stopLoss": "ITiw_xSBxuYP6qNF)F|h",
        "NextTradeCondition": "j4S-npH54+xL{.HbzV3c",
    }
    for variable in variables:
        var_id = variable["@id"]
        var_value = variable["#text"]  # Manter o valor como string
        for key, mapped_id in variable_mappings.items():
            if var_id == mapped_id:
                appState[key] = var_value  # Armazena o valor da variável
    logging.info(
        f"Initialized variables for bot {bot_name}: {appState}")


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
        trade_config[field_name] = field_value
    return trade_config


def get_purchase_conditions_from_editor():
    """Essa função precisa receber os dados do editor da Deriv e extrair
    as condições de compra (IDs das variáveis, operadores de comparação, etc.).

    É importante notar que a implementação dessa função
    depende do formato e da estrutura exata dos dados que o
    editor "Bot" da Deriv envia para o seu código.
    """

    conditions = []
    # ... código para extrair informações de compra da API Deriv Bot
    #  e adicioná-los em 'conditions' (similar a 'extract_purchase_conditions').
    #  Por exemplo, obter os IDs de variáveis, operadores, valores da API do bot

    return conditions


async def run_bot_logic(websocket: WebSocket):
    global deriv_ws
    uri = "wss://ws.binaryws.com/websockets/v3?app_id=1089"
    max_reconnect_attempts = 10
    reconnect_attempts = 0

    try:
        async with websockets.connect(uri, timeout=60) as ws:
            deriv_ws = ws
            logging.info("Connected to Deriv WebSocket")
            reconnect_attempts = 0
            await deriv_ws.send(json.dumps({"authorize": API_TOKEN}))

            # Verificar a autorização antes de enviar outras mensagens
            auth_response = await deriv_ws.recv()
            auth_response = json.loads(auth_response)
            if auth_response.get("error"):
                logging.error(f"Authorization error: {auth_response['error']['message']}")
                return
            else:
                logging.info("Authorized successfully")

            await unsubscribe_ticks()  # Desinscreve dos ticks do bot anterior
            await deriv_ws.send(json.dumps({"balance": 1, "subscribe": 1}))
            await deriv_ws.send(json.dumps(
                {"ticks": appState['trade_config'].get('SYMBOL_LIST', 'R_50'),
                 "subscribe": 1}))
            logging.info(
                f"Subscribed to ticks for symbol: {appState['trade_config'].get('SYMBOL_LIST', 'R_50')}")

            # Loop infinito para manter a conexão WebSocket ativa
            while True:
                response = await deriv_ws.recv()
                response = json.loads(response)
                await handle_deriv_response(websocket, deriv_ws, response)

                # Lógica de compra/venda apenas se o bot estiver ativo e operações não estiverem paradas
                if appState["running"] and not appState["stop_operations"]:
                    await execute_strategy(websocket, deriv_ws)

    except (websockets.ConnectionClosedError, asyncio.TimeoutError,
            Exception) as e:
        logging.error(f"WebSocket connection error: {e}")
        reconnect_attempts += 1
        if reconnect_attempts <= max_reconnect_attempts:
            logging.info(
                f"Attempting to reconnect ({reconnect_attempts}/{max_reconnect_attempts}) in 5 seconds...")
            await asyncio.sleep(5)
        else:
            logging.error("Max reconnect attempts reached. Stopping bot.")
            appState["running"] = False


async def handle_deriv_response(websocket: WebSocket, deriv_ws,
                                response):
    if response.get("error"):
        await websocket.send_text(json.dumps(
            {"type": "error", "message": response["error"]["message"]}))
        logging.error(
            f"Error response from Deriv: {response['error']['message']}")
        return

    msg_type = response.get("msg_type")
    if msg_type == "authorize":
        logging.info("Authorized successfully")
        await deriv_ws.send(json.dumps({"balance": 1, "subscribe": 1}))
        await deriv_ws.send(json.dumps(
            {"ticks": appState['trade_config'].get('SYMBOL_LIST', 'R_50'),
             "subscribe": 1}))
        logging.info(
            f"Subscribed to ticks for symbol: {appState['trade_config'].get('SYMBOL_LIST', 'R_50')}")
    elif msg_type == "balance":
        appState["previousBalance"] = appState["balance"]
        appState["balance"] = float(response["balance"]["balance"])
        if appState["initialBalance"] is None:
            appState["initialBalance"] = appState["balance"]
        balance_change = appState["balance"] - appState["initialBalance"]
        profit_type = "profit" if balance_change >= 0 else "loss"
        await websocket.send_text(json.dumps({
            "type": "balance",
            "balance": f"{appState['balance']:.2f}",
            "balanceChange": f"{balance_change:.2f}",
            "profitType": profit_type
        }))
        logging.info(
            f"Balance updated: {appState['balance']}, Change: {balance_change}")
    elif msg_type == "tick":
        appState["lastTick"] = float(response["tick"]["quote"])
        appState["tick_subscription_id"] = response["tick"]["id"]  # Armazena o ID de inscrição dos ticks
        await websocket.send_text(json.dumps({
            "type": "tick",
            "tick": appState["lastTick"]
        }))
        logging.info(f"Received tick: {appState['lastTick']}")
    elif msg_type == "proposal":
        logging.info(f"Received proposal: {response}")
        if appState["running"] and not appState["stop_operations"]:
            await buy_contract(deriv_ws, response["proposal"]["id"],
                               appState["stake"], websocket)
    elif msg_type == "sell":
        sold_for = float(response["sell"]["sold_for"])
        profit = sold_for - appState["stake"]
        appState["totalProfit"] += profit
        balance_change = appState["balance"] - appState["previousBalance"]
        profit_type = "profit" if balance_change >= 0 else "loss"

        if profit_type == "profit":
            appState["stake"] = appState["initialStake"]
        else:
            appState["stake"] *= appState.get("MartingaleFactor", 2.5)

        appState["NextTradeCondition"] = "Odd" if appState[
                                                      "NextTradeCondition"] == "Even" else "Even"
        logging.info(
            f"Contract finalized. Profit: {profit}, New Stake: {appState['stake']}, Next Trade: {appState['NextTradeCondition']}")

        await websocket.send_text(json.dumps({
            "type": "contract_finalizado",
            "profit": f"{balance_change:.2f}",
            "profitType": profit_type,
            "balance": f"{appState['balance']:.2f}"
        }))
        await websocket.send_text(json.dumps({
            "type": "new_initialAmount",
            "new_initialAmount": f"{appState['stake']:.2f}",
            "profitType": profit_type
        }))


async def request_proposal(deriv_ws, stake, symbol, trade_type, **kwargs):
    await deriv_ws.send(json.dumps({
        "proposal": 1,
        "amount": f"{float(stake):.2f}",
        "basis": "stake",
        "contract_type": trade_type,
        "currency": "USD",
        "duration": 1,
        "duration_unit": "t",
        "symbol": symbol,
        # ... kwargs (restante dos argumentos), adicione a chave para os argumentos do bot
    }))
    response = await deriv_ws.recv()
    response = json.loads(response)
    logging.info(f"Proposal response: {response}")


async def buy_contract(deriv_ws, proposal_id, stake, websocket):
    await deriv_ws.send(json.dumps({
        "buy": proposal_id,
        "price": f"{float(stake):.2f}"
    }))
    response = await deriv_ws.recv()  # Aguarda e recebe a resposta da Deriv
    response = json.loads(
        response)  # Converte para um dicionário
    logging.info(f"Buy contract response: {response}")

    # Adicionado para enviar resposta de compra ao WebSocket do cliente
    if response.get("error"):
        await websocket.send_text(json.dumps(
            {"type": "error", "message": response["error"]["message"]}))
    else:
        await websocket.send_text(json.dumps({
            "type": "buy",
            "contract_id": response.get("buy", {}).get("contract_id"),
            "longcode": response.get("buy", {}).get("longcode"),
        }))


async def execute_strategy(websocket: WebSocket, deriv_ws):
    logging.info(f"Executing strategy - appState: {appState}")

    # Verifica se a estratégia possui condições específicas de compra
    trade_type = appState["strategy"]["trade_config"].get("TRADETYPE_LIST")
    symbol = appState["strategy"]["trade_config"].get("SYMBOL_LIST")

    # Se houver um 'lastTick' válido,
    if appState["lastTick"] is not None:
        if appState["NextTradeCondition"] == "Even":
            # Chama get_purchase_conditions_from_editor para obter as condições
            purchase_conditions = get_purchase_conditions_from_editor()

            # Caso o trade_type e symbol existam, e a condição da API for True:
            if trade_type and symbol and purchase_conditions and check_purchase_conditions(purchase_conditions):
                # Realiza uma compra no estado "Even"
                logging.info(f"Strategy conditions met. Requesting proposal for {trade_type} on {symbol}.")
                try:
                    await request_proposal(deriv_ws, appState["stake"], symbol, trade_type)
                    appState["NextTradeCondition"] = "Odd"  # Mudar para o estado "Odd" após compra
                    logging.info(f"Changed state to Odd")
                except Exception as e:
                    logging.error(f"Error in 'execute_strategy': {e}")
        else:
            if trade_type and symbol:
                logging.info("Next Trade Condition is 'Odd', waiting for new conditions")


# Verificação de condições para compra
def check_purchase_conditions(conditions):
    # Aqui, adicione a sua lógica para comparar as condições
    #  recebidas pelo editor "Bot" com as variáveis do appState
    # (Você deve ter 'conditions' já extraídas pela 'get_purchase_conditions_from_editor'.)

    # Por exemplo, compare 'lastTick' e valores obtidos da função get_purchase_conditions_from_editor()
    for condition in conditions:
        # Realizar a verificação da condição com base nos operadores do XML
        # e  as variáveis que correspondem aos IDs de variáveis.
        #  Por exemplo,  comparar o 'lastTick' (que você obtém de appState)
        #  com um valor definido pelo bot da API Deriv:
        variable_id = condition.get("variable_id")  # Id da variável da condição
        comparison_value = condition.get("comparison_value")
        operator = condition.get("operator")  # Operador (>, <, =, etc.)
        variable_value = appState.get(variable_id)  # Verifique se o ID da variável é reconhecido e extrair seu valor

        # Só processar a condição se o ID da variável for válido:
        if variable_value is not None:
            if operator == "=":
                return variable_value == comparison_value  # Comparação de string
            # ... (outros comparadores, como >, <, >=, <=, se aplicáveis)
            # Use a comparação adequada para os comparadores e tipo de variável

    return False


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)