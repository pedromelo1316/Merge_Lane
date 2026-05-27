# Sugestões para o Coordinator GUI

Ideias de funcionalidades a implementar no futuro.

---

## Alta prioridade

**Botão Abort / Cancel**
Parar o cenário em curso sem fechar a janela. Exige um mecanismo de cancelamento (e.g. `threading.Event` partilhado) que interrompa o `wait_for_done` e limpe os subscribers Zenoh.

**Preview do cenário selecionado**
Ao selecionar um cenário na lista, mostrar um painel lateral ou tooltip com os campos principais: nome, veículos activos, timeout, número de estradas. Evita ter de abrir o JSON para perceber o que cada cenário faz.

**Countdown do timeout**
Durante `wait_for_done`, mostrar um timer regressivo (e.g. `60s → 59s → …`) junto ao nome do cenário em execução. Dá feedback visual de quanto tempo resta antes de avançar.

---

## Média prioridade

**Log por veículo**
Separar o log global em tabs ou sub-painéis por station ID, para facilitar debug quando vários veículos emitem mensagens ao mesmo tempo.

**Highlight do cenário em curso**
Ao correr "Run All", destacar na Listbox o cenário que está a ser executado (e.g. fundo azul), e marcar com um visto (✓) os que já concluíram.

**Histórico de resultados**
Guardar num ficheiro JSON (ou CSV) o resultado de cada corrida: nome do cenário, timestamp de início e fim, veículos que enviaram DONE, veículos que fizeram timeout. Útil para comparar runs.

**Auto-reconnect com retry**
Se a ligação Zenoh falhar ou cair, tentar reconnect automaticamente com backoff exponencial (1 s, 2 s, 4 s, …), em vez de requerer clique manual no botão Reconnect.

---

## Baixa prioridade / nice to have

**Editor de cenário inline**
Permitir editar os campos do JSON directamente na GUI (ex: alterar `timeout_s`, mover posições de veículos) e guardar de volta ao ficheiro antes de correr.

**Filtro / pesquisa de cenários**
Caixa de texto acima da Listbox para filtrar cenários por nome. Útil quando o número de ficheiros JSON crescer.

**Exportar log**
Botão "Save Log" que escreve o conteúdo do painel de log para um ficheiro `.txt` com timestamp no nome.

**Dark mode**
Toggle de tema claro/escuro usando `ttk.Style` com o tema `clam` ou `alt`, para melhor legibilidade em ambiente de lab com luz baixa.

**Suporte a múltiplos Zenoh endpoints**
Campo de texto na status bar para editar o URL Zenoh em runtime, sem ter de definir a variável de ambiente `COORDINATOR_ZENOH_URL` antes de arrancar.
