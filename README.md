# Avant Cloud — Integração Home Assistant

Integração oficial para monitoramento centralizado de instalações Home Assistant pela plataforma **Avant Cloud**.

## Instalação via HACS

1. No Home Assistant, abra o **HACS**
2. Clique em **Integrações** → menu ⋮ → **Repositórios personalizados**
3. Adicione a URL deste repositório e selecione a categoria **Integração**
4. Clique em **Baixar** na integração Avant Cloud
5. Reinicie o Home Assistant

## Configuração

1. Vá em **Configurações → Integrações → Adicionar integração**
2. Pesquise por **Avant Cloud**
3. Preencha:
   - **URL do servidor** — somente a base, sem path (ex: `https://cloud.avantautomacao.com`)
   - **Token de acesso** — gerado no cadastro do servidor no painel Avant Cloud
4. Confirme e reinicie se solicitado

## Dados enviados automaticamente

| Campo | Descrição |
|---|---|
| Versão HA Core | Versão instalada e disponível |
| HAOS / Supervisor | Versão e status de atualização |
| Sistema operacional | Nome e versão do SO do host |
| CPU | Percentual de uso e temperatura |
| Memória RAM | Percentual de uso |
| Disco | Espaço usado e livre (GiB) |
| Swap | Percentual de uso |
| IP local | Endereço IPv4 na rede local |
| Uptime | Tempo desde o último boot |
| Backup | Último backup e próxima execução |
| Entidades / Automações | Totais registrados no HA |
| Rede | Tráfego total acumulado |

## Alterar configurações

Para alterar URL, token ou intervalo de envio após a instalação:

**Configurações → Integrações → Avant Cloud → Configurar**

## Versões

| Versão | Descrição |
|---|---|
| 1.0.0 | Versão inicial |
