using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Build.Locator;
using RoslynService;

// Required: register MSBuild before any Roslyn workspace is created.
MSBuildLocator.RegisterDefaults();

var builder = WebApplication.CreateBuilder(args);

builder.Services.ConfigureHttpJsonOptions(options =>
{
    options.SerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower;
    options.SerializerOptions.DictionaryKeyPolicy = JsonNamingPolicy.SnakeCaseLower;
    options.SerializerOptions.PropertyNameCaseInsensitive = true;
    options.SerializerOptions.DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull;
});

builder.Services.AddSingleton<CSharpAnalyzer>();

var app = builder.Build();

app.MapPost("/analyze/project", async (AnalyzeProjectRequest req, CSharpAnalyzer analyzer) =>
    Results.Ok(await analyzer.AnalyzeProjectAsync(req.ProjectPath)));

app.MapPost("/analyze/file", async (AnalyzeFileRequest req, CSharpAnalyzer analyzer) =>
    Results.Ok(await analyzer.AnalyzeFileAsync(req.FilePath, req.ProjectPath)));

app.MapPost("/cache/invalidate", (InvalidateRequest req, CSharpAnalyzer analyzer) =>
{
    analyzer.InvalidateProject(req.ProjectPath);
    return Results.Ok(new { invalidated = req.ProjectPath });
});

app.MapGet("/health", () =>
    Results.Ok(new { status = "ok", msbuild_loaded = MSBuildLocator.IsRegistered }));

app.Run();

public record AnalyzeProjectRequest(string ProjectPath);
public record AnalyzeFileRequest(string FilePath, string ProjectPath);
public record InvalidateRequest(string ProjectPath);
