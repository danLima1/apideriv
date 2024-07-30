document.addEventListener('DOMContentLoaded', function () {
    const botsList = document.getElementById('bots-list');
    const startBotButton = document.getElementById('start-bot');
    const stopBotButton = document.getElementById('stop-bot');
    const balanceDisplay = document.getElementById('balance');
    const profitLossDisplay = document.getElementById('profit-loss');
    let selectedBot = null;
    let botRunning = false;

    // Função para listar bots disponíveis
    async function fetchBots() {
        const response = await fetch('/api/list-bots');
        const bots = await response.json();
        botsList.innerHTML = '';
        bots.forEach(bot => {
            const li = document.createElement('li');
            li.textContent = bot;
            li.addEventListener('click', () => {
                selectedBot = bot;
                Array.from(botsList.children).forEach(item => item.classList.remove('selected'));
                li.classList.add('selected');
            });
            botsList.appendChild(li);
        });
    }

    // Função para obter o saldo
    async function fetchBalance() {
        const response = await fetch('/api/balance');
        const data = await response.json();
        balanceDisplay.textContent = parseFloat(data.balance).toFixed(2);

        // Atualizar lucro/prejuízo
        profitLossDisplay.textContent = parseFloat(data.total_profit_loss).toFixed(2);
        profitLossDisplay.className = parseFloat(data.total_profit_loss) >= 0 ? 'profit' : 'loss';
    }

    // Função para iniciar o bot
    async function startBot() {
        if (selectedBot && !botRunning) {
            botRunning = true;
            const response = await fetch('/api/buy', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bot_file: selectedBot })
            });
            const data = await response.json();
            console.log(data.message);
        }
    }

    // Função para parar o bot
    function stopBot() {
        // Adicione aqui a lógica para parar o bot
        botRunning = false;
        console.log('Bot parado');
    }

    // Atualização periódica do saldo
    setInterval(fetchBalance, 5000);

    startBotButton.addEventListener('click', startBot);
    stopBotButton.addEventListener('click', stopBot);

    fetchBots();
    fetchBalance();
});
