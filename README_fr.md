# Nova — L'Ordinateur de l'Agent

[English](./README.md) | [中文](./README_zh.md) | [日本語](./README_ja.md) | Français | [Русский](./README_ru.md)

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](./backend/pyproject.toml)
[![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=node.js&logoColor=white)](./Makefile)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

**Nova** est un **agent informatique** full-stack qui recherche, code et crée. Il orchestre des **sous-agents**, de la **mémoire** et des **sandboxes par thread** pour accomplir presque n'importe quelle tâche — propulsé par des **skills extensibles** et une vue en direct du propre ordinateur de l'agent : terminal, éditeur, aperçu de navigateur et progression des tâches, le tout en temps réel.

Nova est construit par **[Ali Technologies](https://www.alilabsx.com)** sur la base du framework open source [DeerFlow](https://github.com/bytedance/deer-flow). La licence MIT en amont et toutes les mentions de copyright originales sont conservées — voir [Licence](#licence) et [NOTICE.md](./NOTICE.md).

> Dites bonjour : *"Je suis Nova, l'agent informatique."*

![Espace Nova — l'agent construit une calculatrice de pourboire et l'aperçoit en direct dans l'onglet Navigateur de l'Agent's Computer](./docs/images/nova-workspace.png)

## Ce que Nova ajoute à DeerFlow

Nova est un refactoring full-stack de DeerFlow 2.0 — **+35 738 lignes sur 338 fichiers** (vérifié : `git diff --shortstat v2.0.0-rc1 HEAD`). Les ajouts principaux, tous construits pour Nova :

- **Agent's Computer** — panneau 6 onglets en direct (Terminal, Éditeur avec diff rouge/vert, Aperçu navigateur, Chronologie d'activité, Fichiers, Revue) diffusant en temps réel ce que l'agent fait.
- **Boucle de vérification** — l'agent teste ses propres builds : Chromium headless auto-vérifie le serveur de dev en cours (erreurs console, détection de rendu vide, captures d'écran), déclenché automatiquement sur le serveur de dev prêt et sur les livrables HTML, avec un chemin visuel pour que le modèle *voie* son build.
- **Revue de code déterministe** — un moteur de revue sans LLM produisant un verdict en langage clair pour les non-développeurs plus des statistiques et drapeaux de risque par fichier pour les développeurs.
- **Middlewares d'auto-correction** — budgets d'itération imposés à l'exécution, détection de boucles mortes, vérification de quota prévol, décontamination des messages d'erreur, progression des tâches en direct.
- **32 outils d'agent** — sessions shell, navigation/clic/saisie/eval navigateur, capture d'écran, scaffold, cycle de vie du serveur de dev, dev_verify, code_review, sauvegarde de skills, et plus.
- **Recherche iGIN0** — client SearXNG renforcé avec retry, disjoncteur, cache LRU+TTL, routage TOR optionnel et piste d'audit de confidentialité.
- **Gestion des modèles en temps réel** — ajoutez/commutez des modèles via l'API et l'interface de configuration sans toucher aux fichiers de config.
- **Couche ops** — watchdog auto-réparation 11 sondes, cycle de vie Docker géré par PM2, persistance après redémarrage.
- **Modèles locaux + gratuits via LiteLLM** — preset Ollama dans les paramètres et proxy LiteLLM géré par PM2 exposant quatre modèles cloud Ollama gratuits (MiniMax M3, Nemotron 3 Super, Qwen3 Coder 480B, GPT-OSS 120B) aux côtés des fournisseurs payants.
- **8 112 lignes de nouveaux tests** répartis dans 37 nouveaux fichiers de tests backend.

DeerFlow upstream fournit le framework agent (sous-agents, mémoire, runtime LangGraph), le système de skills et les sandboxes Docker par thread — crédit au mérite. La cartographie complète et reproductible des attributions se trouve dans **[NOVA_VS_DEERFLOW.md](./NOVA_VS_DEERFLOW.md)**.

## Fonctionne sur du calcul AMD

Nova fournit son inférence sur des **GPU AMD Instinct**, et en fait un choix de premier plan en un clic — construit pour le **Hackathon Développeur AMD (Act II)**.

- **Deux chemins AMD, comme presets** dans Paramètres → Modèles : **Fireworks AI** (géré, servi sur AMD Instinct MI300X) et **Cloud Développeur AMD** (inférence Nova via **vLLM on ROCm**, `scripts/amd-serve-vllm.sh`). Ajouter du calcul AMD est de la configuration, pas du code.
- **Utilisation vérifiable** — `GET /api/models/amd-usage` retourne un résumé machine-liable de l'utilisation AMD, et les modèles AMD affichent un badge **AMD**.
- **Agent Track 1** — un framework batch Fireworks économe en tokens dans [`hackathon/track1/`](./hackathon/track1/) ; build et smoke-test avec `make hackathon-track1`.
- Configuration complète + documentation calcul AMD : **[docs/AMD_INTEGRATION.md](./docs/AMD_INTEGRATION.md)**.

## Table des matières

- [Nova — L'Ordinateur de l'Agent](#nova--lordinateur-de-lagent)
  - [Table des matières](#table-des-matinées)
  - [Configuration Agent en une ligne](#configuration-agent-en-une-ligne)
  - [Démarrage rapide](#démarrage-rapide)
    - [Configuration](#configuration)
    - [Exécution de l'application](#exécution-de-lapplication)
      - [Taille de déploiement](#taille-de-déploiement)
      - [Option 1 : Docker (Recommandé)](#option-1-docker-recommandé)
      - [Option 2 : Développement local](#option-2-développement-local)
    - [Avancé](#avancé)
      - [Mode sandbox](#mode-sandbox)
      - [Serveur MCP](#serveur-mcp)
      - [Canaux IM](#canaux-im)
      - [Traçage LangSmith](#traçage-langsmith)
      - [Traçage Langfuse](#traçage-langfuse)
  - [De la Recherche Approfondie au Framework Super Agent](#de-la-recherche-approfondie-au-framework-super-agent)
  - [Fonctionnalités Principales](#fonctionnalités-principales)
    - [Skills et Outils](#skills-et-outils)
    - [Sous-Agents](#sous-agents)
    - [Sandbox et Système de Fichiers](#sandbox-et-système-de-fichiers)
    - [Ingénierie du Contexte](#ingénierie-du-contexte)
    - [Mémoire à Long Terme](#mémoire-à-long-terme)
  - [Modèles Recommandés](#modèles-recommandés)
  - [Client Python Intégré](#client-python-intégré)
  - [Documentation](#documentation)
  - [⚠️ Avis de Sécurité](#️-avis-de-sécurité)
  - [Contribution](#contribution)
  - [Licence](#licence)

## Configuration Agent en une ligne

Si vous utilisez Claude Code, Codex, Cursor, Windsurf ou un autre agent de code, vous pouvez lui remettre les instructions de configuration en une phrase :

```text
Help me clone Nova if needed, then bootstrap it for local development by following https://raw.githubusercontent.com/Jahanzaib211/nova/main/docs/Install.md
```

Ce prompt est destiné aux agents de code. Il dit à l'agent de cloner le dépôt si nécessaire, de choisir Docker quand disponible, et de s'arrêter avec la commande exacte suivante plus toute configuration manquante.

## Démarrage rapide

### Configuration

1. **Cloner le dépôt Nova**

   ```bash
   git clone https://github.com/Jahanzaib211/nova.git
   cd nova
   ```

2. **Lancer l'assistant de configuration**

   Depuis la racine du projet (`nova/`) :

   ```bash
   make setup
   ```

   Cela lance un assistant interactif qui vous guide dans le choix du fournisseur LLM, de la recherche web optionnelle et des préférences d'exécution/sécurité. Il génère un `config.yaml` minimal et écrit vos clés dans `.env`. Environ 2 minutes.

   Lancez `make doctor` à tout moment pour vérifier votre installation et obtenir des conseils de correction.

### Exécution de l'application

#### Taille de déploiement

| Cible de déploiement | Point de départ | Recommandé | Notes |
|---------|-----------|------------|-------|
| Évaluation locale / `make dev` | 4 vCPU, 8 Go RAM, 20 Go SSD | 8 vCPU, 16 Go RAM | Pour un développeur ou une session légère |
| Développement Docker / `make docker-start` | 4 vCPU, 8 Go RAM, 25 Go SSD | 8 vCPU, 16 Go RAM | Les builds d'images et montages ont besoin de plus d'espace |
| Serveur longue durée / `make up` | 8 vCPU, 16 Go RAM, 40 Go SSD | 16 vCPU, 32 Go RAM | Préféré pour l'usage partagé ou les charges lourdes |

#### Option 1 : Docker (Recommandé)

**Développement** (rechargement à chaud, montages source) :

```bash
make docker-init    # Télécharger l'image sandbox (une seule fois ou lors de la mise à jour)
make docker-start   # Démarrer les services (détection automatique du mode sandbox)
```

> [!TIP]
> Sur Linux, si les commandes Docker échouent avec `permission denied`, ajoutez votre utilisateur au groupe `docker` et re-connectez-vous. Voir [CONTRIBUTING.md](CONTRIBUTING.md#linux-docker-daemon-permission-denied) pour la correction complète.

**Production** (build local des images, montage de la config et des données) :

```bash
make up     # Construire les images et démarrer tous les services
make down   # Arrêter et supprimer les conteneurs
```

Accès : http://localhost:2026

#### Option 2 : Développement local

1. **Vérifier les prérequis** :
   ```bash
   make check  # Vérifie Node.js 22+, pnpm, uv, nginx
   ```

2. **Installer les dépendances** :
   ```bash
   make install  # Installe les dépendances backend + frontend + hooks pre-commit
   ```

3. **Démarrer les services** :
   ```bash
   make dev
   ```

4. **Accès** : http://localhost:2026

### Avancé

#### Mode sandbox

Nova supporte plusieurs modes d'exécution sandbox :
- **Exécution locale** (code exécuté directement sur l'hôte)
- **Exécution Docker** (code exécuté dans des conteneurs Docker isolés)
- **Docker + Kubernetes** (code exécuté dans des Pods via le service provisioner)

Voir le [Guide de Configuration Sandbox](backend/docs/CONFIGURATION.md#sandbox) pour configurer votre mode préféré.

#### Serveur MCP

Nova supporte des serveurs MCP et des skills configurables pour étendre ses capacités. Les serveurs MCP HTTP/SSE supportent les flux OAuth (`client_credentials`, `refresh_token`). Voir le [Guide Serveur MCP](backend/docs/MCP_SERVER.md) pour les instructions détaillées.

#### Canaux IM

Nova supporte la réception de tâches depuis des applications de messagerie. Les canaux démarrent automatiquement quand configurés — aucune IP publique requise.

| Canal | Transport | Difficulté |
|---------|-----------|------------|
| Telegram | Bot API (long-polling) | Facile |
| Slack | Socket Mode | Modéré |
| Feishu / Lark | WebSocket | Modéré |
| WeChat | Tencent iLink (long-polling) | Modéré |
| WeCom | WebSocket | Modéré |
| DingTalk | Stream Push (WebSocket) | Modéré |

Configurez dans `config.yaml` et définissez les clés API correspondantes dans `.env`.

#### Traçage LangSmith

Nova intègre [LangSmith](https://smith.langchain.com). Quand activé, tous les appels LLM, exécutions d'agents et exécutions d'outils sont tracés et visibles dans le tableau de bord LangSmith.

```bash
LANGSMITH_TRACING=true
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_API_KEY=lsv2_pt_xxxxxxxxxxxxxxxx
LANGSMITH_PROJECT=xxx
```

#### Traçage Langfuse

Nova supporte également l'observabilité [Langfuse](https://langfuse.com).

```bash
LANGFUSE_TRACING=true
LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxxxxxxxxxx
LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxxxxxxxxxx
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

## De la Recherche Approfondie au Framework Super Agent

Nova a commencé comme un framework de recherche approfondie — et la communauté l'a poussé plus loin. Les développeurs l'ont utilisé pour construire des pipelines de données, générer des présentations, créer des tableaux de bord, automatiser des flux de contenu. Nous ne l'avions pas anticipé.

Cela nous a dit quelque chose d'important : Nova n'était pas seulement un outil de recherche. C'était un **framework** — un runtime qui donne aux agents l'infrastructure pour accomplir le travail.

Donc nous l'avons reconstruit de zéro.

Nova 2.0 n'est plus un framework à assembler. C'est un super agent framework — batteries incluses, entièrement extensible. Construit sur LangGraph et LangChain, il fournit tout ce dont un agent a besoin dès le départ : un système de fichiers, de la mémoire, des skills, une exécution sandbox-aware, et la capacité de planifier et de générer des sous-agents pour des tâches complexes multi-étapes.

Utilisez-le tel quel. Ou déconstruisez-le et personnalisez-le.

## Fonctionnalités Principales

### Skills et Outils

Les skills sont ce qui permet à Nova de faire *presque tout*.

Un skill standard d'agent est un module de capacité structuré — un fichier Markdown qui définit un workflow, des meilleures pratiques et des références à des ressources de support. Nova ship avec des skills intégrés pour la recherche, la génération de rapports, la création de slides, les pages web, la génération d'images et de vidéos, et plus. Mais le vrai pouvoir est l'extensibilité : ajoutez vos propres skills, remplacez les intégrés, ou combinez-les en workflows composites.

Les skills sont chargés progressivement — uniquement quand la tâche en a besoin, pas tous en une fois. Cela garde la fenêtre de contexte légère et fait fonctionner Nova même avec des modèles sensibles aux tokens.

Les outils suivent la même philosophie. Nova ship avec un ensemble d'outils de base — recherche web, fetch web, opérations fichiers, exécution bash — et supporte les outils personnalisés via les serveurs MCP et les fonctions Python. Remplacez tout. Ajoutez tout.

### Sous-Agents

Les tâches complexes tiennent rarement en un seul passage. Nova les décompose.

L'agent principal peut générer des sous-agents à la volée — chacun avec son propre contexte, outils et conditions de terminaison. Les sous-agents s'exécutent en parallèle quand possible, retournent des résultats structurés, et l'agent principal synthétise tout en une sortie cohérente.

C'est comme Nova gère les tâches de plusieurs minutes à plusieurs heures : une tâche de recherche peut se disperser en une douzaine de sous-agents, chacun explorant un angle différent, puis converger en un seul rapport — ou un site web — ou une présentation avec des visuels générés. Un agent, plusieurs mains.

### Sandbox et Système de Fichiers

Nova ne se contente pas de *parler* de faire des choses. Il a son propre ordinateur.

Chaque tâche obtient son propre environnement d'exécution avec une vue complète du système de fichiers — skills, espace de travail, uploads, livrables. L'agent lit, écrit et édite des fichiers. Il peut voir des images et, quand configuré en toute sécurité, exécuter des commandes shell.

Avec `AioSandboxProvider`, l'exécution shell se fait dans des conteneurs isolés. Avec `LocalSandboxProvider`, les outils fichiers mappent toujours vers des répertoires par thread sur l'hôte, mais le `bash` hôte est désactivé par défaut car ce n'est pas un frontière d'isolamento sécurisée.

C'est la différence entre un chatbot avec accès aux outils et un agent avec un véritable environnement d'exécution.

### Ingénierie du Contexte

**Contexte Sous-Agent Isolé** : Chaque sous-agent s'exécute dans son propre contexte isolé. Cela signifie que le sous-agent ne peut pas voir le contexte de l'agent principal ou des autres sous-agents.

**Résumé** : Dans une session, Nova gère agressivement le contexte — résumant les sous-tâches terminées, déchargeant les résultats intermédiaires sur le filesystem, compressant ce qui n'est plus immédiatement pertinent. Cela lui permet de rester affûté sur de longues tâches multi-étapes sans exploser la fenêtre de contexte.

**Récupération Stricte des Appels d'Outils** : Quand un fournisseur ou middleware interrompt une boucle d'appels d'outils, Nova dépouille maintenant les métadonnées brutes de niveau fournisseur sur les messages assistant à arrêt forcé et injecte des résultats d'outils placeholder pour les appels en attente avant la prochaine invocation du modèle.

### Mémoire à Long Terme

La plupart des agents oublient tout à la fin d'une conversation. Nova se souvient.

Au fil des sessions, Nova construit une mémoire persistante de votre profil, préférences et connaissances accumulées. Plus vous l'utilisez, mieux il vous connaît — votre style d'écriture, votre stack technique, vos workflows récurrents. La mémoire est stockée localement et reste sous votre contrôle.

## Modèles Recommandés

Nova est agnostique au modèle — il fonctionne avec n'importe quel LLM implémentant l'API compatible OpenAI. Cela dit, il performe mieux avec les modèles qui supportent :

- **Fenêtres de contexte longues** (100k+ tokens) pour la recherche approfondie et les tâches multi-étapes
- **Capacités de raisonnement** pour la planification adaptative et la décomposition complexe
- **Entrées multimodales** pour la compréhension d'images et de vidéos
- **Fort usage des outils** pour les appels de fonctions fiables et les sorties structurées

## Client Python Intégré

Nova peut être utilisé comme bibliothèque Python intégrée sans exécuter les services HTTP complets. Le `DeerFlowClient` fournit un accès direct en-processus à toutes les capacités agent et Gateway, retournant les mêmes schémas de réponse que l'API HTTP Gateway :

```python
from deerflow.client import DeerFlowClient

client = DeerFlowClient()

# Chat
response = client.chat("Analysez cet article pour moi", thread_id="my-thread")

# Streaming (protocole SSE LangGraph : values, messages-tuple, end)
for event in client.stream("hello"):
    if event.type == "messages-tuple" and event.data.get("type") == "ai":
        print(event.data["content"])

# Configuration et gestion — retourne des dicts alignés Gateway
models = client.list_models()        # {"models": [...]}
skills = client.list_skills()        # {"skills": [...]}
client.update_skill("web-search", enabled=True)
client.upload_files("thread-1", ["./report.pdf"])  # {"success": True, "files": [...]}
```

Voir `backend/packages/harness/deerflow/client.py` pour la documentation API complète.

## Documentation

- [Guide de Contribution](CONTRIBUTING.md) - Mise en place de l'environnement de développement et flux de travail
- [Guide de Configuration](backend/docs/CONFIGURATION.md) - Instructions de configuration
- [Vue d'Ensemble Architecture](backend/CLAUDE.md) - Détails de l'architecture technique
- [Architecture Backend](backend/README.md) - Architecture backend et référence API
- [Audit Entreprise](docs/AUDIT.md) - Audit full-stack et plan d'amélioration
- [Audit Frontend](docs/FRONTEND_AUDIT.md) - Arbre de composants frontend et flux de données

## ⚠️ Avis de Sécurité

### Un Déploiement Inadapté Peut Introduire des Risques de Sécurité

Nova possède des capacités à haut privilège, y compris **l'exécution de commandes système, les opérations de ressources et l'invocation de logique métier**, et est conçu par défaut pour être **déployé dans un environnement local de confiance (accessible uniquement via l'interface boucle de retour 127.0.0.1)**. Si vous déployez l'agent dans des environnements non fiables — tels que des réseaux LAN, des serveurs cloud publics ou d'autres environnements accessibles multi-points — sans mesures de sécurité strictes, cela peut introduire des risques de sécurité.

**Nous recommandons fortement de déployer Nova dans un environnement réseau local de confiance.** Si un déploiement inter-appareils ou inter-réseau est nécessaire, vous devez implémenter des mesures de sécurité strictes telles que des listes blanches IP, une passerelle d'authentification et une isolation réseau.

## Contribution

Nous accueillons les contributions ! Veuillez consulter [CONTRIBUTING.md](CONTRIBUTING.md) pour la mise en place du développement, le flux de travail et les directives.

## Licence

La fondation DeerFlow de ce projet est open source sous la [Licence MIT](./LICENSE). Le texte de la licence originale et toutes les mentions de copyright en amont sont conservés intacts et non modifiés.

Les ajouts spécifiques à Nova par Ali Technologies (l'interface Agent's Computer, l'isolation de conteneurs par thread, les watchdogs, les reçus et outils connexes) sont **propriétaires d'Ali Technologies** — voir [NOTICE.md](./NOTICE.md).

## Remerciements

Nova est construit sur [DeerFlow](https://github.com/bytedance/deer-flow) par ByteDance et sa communauté. Nous sommes profondément reconnaissants envers les projets et contributeurs dont le travail rend Nova possible — vraiment, nous nous tenons sur les épaules de géants.

- **[DeerFlow](https://github.com/bytedance/deer-flow)** : Le framework super agent sur lequel Nova est construit.
- **[LangChain](https://github.com/langchain-ai/langchain)** : Alimente nos interactions LLM et chaînes.
- **[LangGraph](https://github.com/langchain-ai/langgraph)** : Permet l'orchestration multi-agent sophistiquée.
