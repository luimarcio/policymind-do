# Roteiro do vídeo — InsurMinds_Projeto_Final.mp4 (máx. 5 minutos)

**Formato sugerido:** gravação de tela com narração. Slides do pitch deck nas partes 1, 2 e 5; aplicação rodando nas partes 3 e 4.

**Antes de gravar:**

- Abra o PowerShell na pasta do projeto, ative o `.venv` e rode `streamlit run app.py`.
- Rode a análise uma vez antes de gravar. As extrações ficam no cache e a demonstração fica rápida.
- Feche abas e notificações e deixe o navegador em tela cheia (F11).
- Confira que o `.env` e a chave **não** aparecem na tela.
- Ferramentas de gravação gratuitas: Xbox Game Bar (Win+Alt+R), Clipchamp (já vem no Windows 11) ou OBS Studio.

---

## Parte 1: O problema (0:00 a 0:40) · slides 1 e 2

**Tela:** capa e depois o slide "Comparar apólices D&O ainda é manual e lento".

> "Olá! Somos o Márcio, o Mauro e o Pedro, e este é o PolicyMind D&O, nosso projeto final do curso InsurMinds.
> Apólices de seguro D&O são documentos longos: as Condições Gerais que usamos têm de 52 a 104 páginas, em linguagem jurídica, e cada seguradora usa termos diferentes para a mesma cobertura. Comparar uma renovação com a apólice anterior exige horas de leitura de um especialista."

## Parte 2: A solução e a arquitetura (0:40 a 1:40) · slides 3, 4 e 5

**Tela:** slide da solução, depois arquitetura, depois guardrails.

> "O PolicyMind recebe duas apólices em PDF ou imagem, extrai os dados com IA generativa, compara de forma determinística e entrega uma síntese executiva.
> A arquitetura é multiagente, com um orquestrador e cinco agentes. O DocumentAgent lê o PDF e só usa OCR quando a página não tem texto. O PolicyExtractionAgent usa um LLM para gerar um JSON padronizado. O NormalizationAgent padroniza os termos entre seguradoras. O ComparisonAgent compara campo a campo. E o AnalysisAgent escreve a síntese.
> O nosso princípio é: na dúvida, o sistema declara a ausência do dado em vez de concluir. Cada valor vem com a página e o trecho de origem, e se o trecho não existe no PDF o valor é descartado."

## Parte 3: Demonstração (1:40 a 3:40) · aplicação

**Tela:** navegador em `localhost:8501`.

1. **(1:40)** Mostre a barra lateral: *"IA generativa ativa, modelo gpt-oss-120b no Groq."*
2. **(1:50)** Ligue **"Usar arquivos da pasta data/"** e mostre as duas apólices: *"Um caso real: a mesma empresa trocou a Tokio Marine pela AXA na renovação."*
3. **(2:00)** Clique em **Analisar e comparar** e mostre os cartões ficando verdes: *"O orquestrador mostra o status e o tempo de cada agente."*
4. **(2:20)** Role até o **Resumo dos documentos**: seguradora, prêmio e vigência de cada apólice.
5. **(2:35)** **Síntese executiva**: leia a primeira frase. *"Texto gerado pela IA. Repare que os ganhos são só as reduções de custo: o LMG e a franquia aparecem como informação que falta na apólice A, não como ganho."*
6. **(2:55)** Aba **Prêmio**: *"Queda de 48,8%: de R$ 10.880,98 para R$ 5.571,59. Ao lado, a página e o trecho de onde veio cada valor."*
7. **(3:10)** Aba **Extensões**: *"A AXA tem 18 extensões, mas o arquivo da Tokio Marine só traz a primeira folha. Por isso o sistema marca 'sem base de comparação' em vez de dizer que algo foi adicionado."*
8. **(3:25)** Aba **Pontos de Atenção** e o expander **JSON estruturado extraído**: *"Tudo é auditável."*

## Parte 4: Resultados (3:40 a 4:30) · slides 6, 9 e 10

**Tela:** slides "Caso real", "Resultados técnicos" e "O que a validação real nos ensinou".

> "Nos resultados, os cinco agentes concluíram com IA em menos de 30 segundos, e o projeto tem 33 testes automatizados.
> A validação real também nos ensinou bastante. O plano gratuito do Groq limitava o tamanho do pedido, e passamos a enviar só as páginas críticas. A IA chegou a classificar como 'ganho' um dado que não existia na outra apólice, e criamos uma regra para tratar isso como ponto de atenção."

## Parte 5: Próximos passos e encerramento (4:30 a 5:00) · slides 11 e 12

> "Como evolução, queremos ligar cada apólice às suas Condições Gerais com RAG e permitir perguntas em linguagem natural, sempre citando a página.
> O código está no GitHub, com README e relatório técnico. Lembrando que é um projeto acadêmico, que não substitui o corretor, a seguradora ou um parecer jurídico. Obrigado!"

---

**Ao terminar:** salve como **`InsurMinds_Projeto_Final.mp4`** e coloque na pasta `Projeto_Final_Artefatos`. Se o arquivo passar de 25 MB, o upload pelo site do GitHub não aceita. Nesse caso, publique o vídeo no YouTube (não listado) ou no Google Drive e coloque o link no README.
