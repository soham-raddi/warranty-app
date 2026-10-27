import re

from flask import Blueprint, g, jsonify, request

from ..db import (
    clear_chat_messages,
    delete_chat_conversation,
    get_chat_conversations,
    get_chat_messages,
    save_chat_message,
    search_chat_messages,
)
from ..services.chatbot import ask_digital_twin
from ..services.ratelimit import limit

chat_bp = Blueprint('chat', __name__)

# Number of earlier messages replayed to the model for context
CHAT_CONTEXT_MESSAGES = 20
CONVERSATION_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


@chat_bp.route('/api/chat', methods=['POST'])
@limit('chat', minute=10, hourly=60, daily=200)
def chat():
    data = request.json or {}
    user_message = str(data.get('message') or '').strip()
    conversation_id = data.get('conversation_id', 'legacy')

    if not user_message:
        return jsonify({'error': 'Type a message first.'}), 400
    if len(user_message) > 2000:
        return jsonify({'error': 'Messages can be at most 2,000 characters.'}), 400
    if not CONVERSATION_ID_PATTERN.match(str(conversation_id)):
        return jsonify({'error': 'That conversation could not be found.'}), 400

    # History comes from the database, not the client, so it cannot be tampered with.
    owner = (g.workspace_id, g.current_user['id'])
    history = get_chat_messages(*owner, conversation_id=conversation_id, limit=CHAT_CONTEXT_MESSAGES)
    save_chat_message(*owner, 'user', user_message, conversation_id=conversation_id)

    result = ask_digital_twin(user_message, history, g.workspace_id)

    if isinstance(result, dict):
        assistant_reply = result.get('reply', '')
        # Failed replies are shown to the user but not stored as part of the conversation.
        if assistant_reply and not result.get('error'):
            save_chat_message(*owner, 'assistant', assistant_reply, conversation_id=conversation_id)
        return jsonify({
            'reply': assistant_reply,
            'action': result.get('action'),
            'error': bool(result.get('error')),
        })

    assistant_reply = str(result)
    if assistant_reply:
        save_chat_message(*owner, 'assistant', assistant_reply, conversation_id=conversation_id)
    return jsonify({'reply': assistant_reply, 'action': None})


@chat_bp.route('/api/chat/history', methods=['GET'])
def chat_history():
    conversation_id = request.args.get('conversation_id', default='legacy', type=str)
    limit = request.args.get('limit', default=100, type=int)
    query = request.args.get('query', default='', type=str)

    if query and query.strip():
        messages = search_chat_messages(g.workspace_id, g.current_user['id'], query=query, conversation_id=conversation_id, limit=limit)
    else:
        messages = get_chat_messages(g.workspace_id, g.current_user['id'], conversation_id=conversation_id, limit=limit)

    return jsonify({'messages': messages})


@chat_bp.route('/api/chat/conversations', methods=['GET'])
def chat_conversations():
    limit = request.args.get('limit', default=100, type=int)
    query = request.args.get('query', default='', type=str)
    conversations = get_chat_conversations(g.workspace_id, g.current_user['id'], query=query, limit=limit)
    return jsonify({'conversations': conversations})


@chat_bp.route('/api/chat/conversations/<conversation_id>/messages', methods=['GET'])
def chat_conversation_messages(conversation_id):
    limit = request.args.get('limit', default=100, type=int)
    messages = get_chat_messages(g.workspace_id, g.current_user['id'], conversation_id=conversation_id, limit=limit)
    return jsonify({'messages': messages, 'conversation_id': conversation_id})


@chat_bp.route('/api/chat/conversations/<conversation_id>', methods=['DELETE'])
def delete_conversation(conversation_id):
    if not CONVERSATION_ID_PATTERN.match(conversation_id):
        return jsonify({'error': 'That conversation could not be found.'}), 404
    if not delete_chat_conversation(g.workspace_id, g.current_user['id'], conversation_id):
        return jsonify({'error': 'That conversation could not be found.'}), 404
    return jsonify({'message': 'Chat deleted.'})


@chat_bp.route('/api/chat/history', methods=['DELETE'])
def clear_chat_history():
    # Chats are private, so anyone may clear their own history (and only their own).
    clear_chat_messages(g.workspace_id, g.current_user['id'])
    return jsonify({'message': 'Chat history cleared.'})