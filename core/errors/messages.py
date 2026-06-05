"""Map exceptions and Discord errors to safe user-visible strings."""
from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from core.errors.exceptions import UserFacingError

DEFAULT_UNEXPECTED = "Something went wrong. Please try again later."


def user_message_for(error: BaseException) -> str:
    if isinstance(error, UserFacingError):
        return error.user_message

    if isinstance(error, app_commands.CommandOnCooldown):
        retry = int(error.retry_after)
        unit = "second" if retry == 1 else "seconds"
        return f"This command is on cooldown. Try again in {retry} {unit}."

    if isinstance(error, app_commands.MissingPermissions):
        return "You don't have the required permissions to use this command."

    if isinstance(error, app_commands.BotMissingPermissions):
        return "I don't have the required permissions to run this command."

    if isinstance(error, app_commands.CheckFailure):
        return str(error) or "You cannot use this command right now."

    if isinstance(error, app_commands.TransformerError):
        return "Invalid input. Please check your options and try again."

    if isinstance(error, app_commands.CommandInvokeError) and error.original:
        return user_message_for(error.original)

    if isinstance(error, commands.CommandOnCooldown):
        retry = int(error.retry_after)
        unit = "second" if retry == 1 else "seconds"
        return f"This command is on cooldown. Try again in {retry} {unit}."

    if isinstance(error, commands.MissingPermissions):
        return "You don't have the required permissions to use this command."

    if isinstance(error, commands.BotMissingPermissions):
        return "I don't have the required permissions to run this command."

    if isinstance(error, commands.CheckFailure):
        return str(error) or "You cannot use this command right now."

    if isinstance(error, commands.CommandInvokeError) and error.original:
        return user_message_for(error.original)

    if isinstance(error, commands.UserInputError):
        return str(error) or "Invalid command usage."

    if isinstance(error, discord.Forbidden):
        return "I don't have permission to complete that action."

    if isinstance(error, discord.NotFound):
        return "That channel, message, or user could not be found."

    if isinstance(error, discord.HTTPException):
        return _http_exception_message(error)

    if isinstance(error, asyncio.TimeoutError):
        return "The request timed out. Please try again later."

    return DEFAULT_UNEXPECTED


def _http_exception_message(error: discord.HTTPException) -> str:
    status = getattr(error, "status", None) or getattr(error, "code", None)
    if status == 429:
        return "Discord rate limit reached. Please wait a moment and try again."
    if status == 403:
        return "I don't have permission to complete that action."
    if status == 404:
        return "That resource could not be found."
    if status and int(status) >= 500:
        return "Discord is having issues. Please try again later."
    return DEFAULT_UNEXPECTED


def external_service_message(exc: BaseException) -> str:
    """Map requests/aiohttp/DB errors to user text."""
    try:
        import aiohttp
    except ImportError:
        aiohttp = None  # type: ignore

    try:
        import requests
    except ImportError:
        requests = None  # type: ignore

    if isinstance(exc, asyncio.TimeoutError):
        return "The service is taking too long to respond. Please try again later."

    if aiohttp and isinstance(exc, aiohttp.ClientError):
        return "Unable to connect to an external service. Please try again later."

    if requests and isinstance(exc, requests.exceptions.Timeout):
        return "The service is taking too long to respond. Please try again later."

    if requests and isinstance(exc, requests.exceptions.RequestException):
        return "Unable to connect to an external service. Please try again later."

    err_name = type(exc).__name__.lower()
    if "operational" in err_name or "database" in err_name or "mysql" in err_name:
        return "Database is temporarily unavailable. Please try again later."

    return user_message_for(exc)
