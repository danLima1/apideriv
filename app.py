import os
import json
import uvicorn
import xmltodict
import asyncio
import websockets
import pdb  # Para usar o debugger
from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pathlib import Path
from pydantic import BaseModel
import logging

load_dotenv()

API_TOKEN = os.getenv("API_TOKEN")
PORT = int(os.getenv("PORT", 3001))

if not API_TOKEN:
    raise ValueError("API_TOKEN não está definida nas variáveis de ambiente. "
                     "Crie um arquivo .env e defina a variável API_TOKEN.")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    "NextTradeCondition": "Even"
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
                await start_bot_logic(websocket, bot_name)
            elif command == "stop":
                appState["running"] = False
                logging.info("Stopping bot")
                await websocket.send_text(
                    json.dumps({"type": "status", "message": "Bot stopped"}))
    except websockets.ConnectionClosedError as e:
        logging.error(f"WebSocket connection closed: {str(e)}")
    except Exception as e:
        logging.error(f"Error during WebSocket connection: {str(e)}")


async def start_bot_logic(websocket: WebSocket, bot_name: str):
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

    appState["running"] = True
    logging.info(f"Bot {bot_name} is running with config: {appState}")
    await run_bot_logic(websocket)


def initialize_variables(variables, bot_name):
    variable_mappings = {
        "stake": "gsQah}04l(VNTYJ`*;{Y",
        "initialStake": "gsQah}04l(VNTYJ`*;{Y",
        "MartingaleFactor": "...",  # Coloque o ID correto da variável MartingaleFactor
        "targetProfit": "oQ~TQa?Dq78]MM},5;*l",
        "stopLoss": "ITiw_xSBxuYP6qNF)F|h",
        "NextTradeCondition": "j4S-npH54+xL{.HbzV3c",
    }
    for variable in variables:
        var_id = variable["@id"]
        var_value = float(variable["#text"]) if variable["#text"] else 0.0
        for key, mapped_id in variable_mappings.items():
            if var_id == mapped_id:
                appState[key] = var_value
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


def extract_purchase_conditions(purchase_block):
    conditions = []
    if 'statement' in purchase_block and 'BEFOREPURCHASE_STACK' in purchase_block['statement']:
        stack_block = purchase_block['statement']['BEFOREPURCHASE_STACK']['block']
        if stack_block['@type'] == 'controls_if':
            # Corrigido: Navegação correta pela estrutura do XML
            condition_block = stack_block['value']['IF0']['block']
            conditions.append(extract_condition(condition_block))
            # Corrigido: Verificação e extração da condição 'ELSE'
            if 'ELSE' in stack_block['statement']:
                else_condition_block = stack_block['statement']['ELSE']['block']
                conditions.append(extract_condition(else_condition_block))
    return conditions


def extract_condition(condition_block):
    condition = {}
    if condition_block['@type'] == 'logic_compare':
        variable_id = condition_block['value']['A']['block']['field']['@id']
        comparison_value = \
            condition_block['value']['B']['block']['field']['#text']
        condition = {
            'variable_id': variable_id,
            'comparison_value': comparison_value
        }
    return condition


async def run_bot_logic(websocket: WebSocket):
    uri = "wss://ws.binaryws.com/websockets/v3?app_id=1089"
    max_reconnect_attempts = 10
    reconnect_attempts = 0

    while appState["running"]:
        try:
            async with websockets.connect(uri, timeout=60) as deriv_ws:
                logging.info("Connected to Deriv WebSocket")
                reconnect_attempts = 0  # Reseta as tentativas após reconexão
                await deriv_ws.send(json.dumps({"authorize": API_TOKEN}))
                async for message in deriv_ws:
                    response = json.loads(message)
                    await handle_deriv_response(websocket, deriv_ws,
                                                response)
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
        await websocket.send_text(json.dumps({
            "type": "tick",
            "tick": appState["lastTick"]
        }))
        logging.info(f"Received tick: {appState['lastTick']}")
    elif msg_type == "proposal":
        logging.info(f"Received proposal: {response}")
        if appState["running"]:
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

        await execute_strategy(websocket, deriv_ws)


async def request_proposal(deriv_ws, stake, symbol, trade_type):
    await deriv_ws.send(json.dumps({
        "proposal": 1,
        "amount": f"{float(stake):.2f}",
        "basis": "stake",
        "contract_type": trade_type,
        "currency": "USD",
        "duration": 1,
        "duration_unit": "t",
        "symbol": symbol
    }))
    response = await deriv_ws.recv()  # Aguarda e recebe a resposta da Deriv
    response = json.loads(
        response)  # Converte para um dicionário
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


async def execute_strategy(websocket: WebSocket, deriv_ws):
    logging.info(f"Executing strategy - appState: {appState}")

    # Lógica de compra direta (sem condições adicionais):
    if appState["NextTradeCondition"] == "Even":
        trade_type = appState["strategy"]["trade_config"].get("TRADETYPE_LIST")
    elif appState["NextTradeCondition"] == "Odd":
        trade_type = appState["strategy"]["trade_config"].get("TRADETYPE_LIST")

    if trade_type:
        try:
            logging.info(f"Sending proposal: {trade_type}, Symbol: {appState['trade_config'].get('SYMBOL_LIST')}")
            await request_proposal(deriv_ws, appState["stake"], appState['trade_config'].get('SYMBOL_LIST'), trade_type)
        except Exception as e:
            logging.error(f"Error in 'execute_strategy': {e}")



if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)