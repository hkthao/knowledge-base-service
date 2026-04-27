using System.Collections.Concurrent;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;
using Microsoft.CodeAnalysis.MSBuild;
using Microsoft.CodeAnalysis.Text;
using RoslynService.Models;

namespace RoslynService;

public class CSharpAnalyzer
{
    // Per-project workspace cache. Lazy<Task<...>> ensures concurrent requests
    // for the same project share a single MSBuild load instead of racing.
    private readonly ConcurrentDictionary<string, Lazy<Task<ProjectCacheEntry>>> _cache = new();

    // Internal namespace prefixes whose calls we want to keep even if the
    // symbol's location is in metadata (e.g. shared internal NuGet packages).
    // Comma-separated INTERNAL_NS_PREFIXES env var overrides.
    private readonly string[] _internalNamespaces = (
        Environment.GetEnvironmentVariable("INTERNAL_NS_PREFIXES") ?? ""
    ).Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

    private record ProjectCacheEntry(MSBuildWorkspace Workspace, ProjectId ProjectId);

    // ── Public API ────────────────────────────────────────────────────

    public async Task<AnalysisResult> AnalyzeProjectAsync(string projectPath)
    {
        var entry = await GetOrLoadAsync(projectPath);
        var project = entry.Workspace.CurrentSolution.GetProject(entry.ProjectId)
            ?? throw new InvalidOperationException($"Project not found: {projectPath}");
        var compilation = await project.GetCompilationAsync()
            ?? throw new InvalidOperationException("Compilation failed");

        var entities = new List<EntityDto>();
        var relations = new List<RelationDto>();
        foreach (var doc in project.Documents.Where(d => d.FilePath?.EndsWith(".cs") == true))
        {
            var result = await AnalyzeDocumentAsync(doc, compilation);
            entities.AddRange(result.Entities);
            relations.AddRange(result.Relations);
        }
        return new AnalysisResult { Entities = entities, Relations = relations };
    }

    public async Task<AnalysisResult> AnalyzeFileAsync(string filePath, string projectPath)
    {
        var entry = await GetOrLoadAsync(projectPath);

        // The file on disk may have changed since we cached the project.
        // Apply the current text to the workspace so the Compilation reflects it.
        var solution = entry.Workspace.CurrentSolution;
        var project = solution.GetProject(entry.ProjectId)
            ?? throw new InvalidOperationException($"Project not found: {projectPath}");
        var doc = project.Documents.FirstOrDefault(d => d.FilePath == filePath)
            ?? throw new FileNotFoundException($"File not found in project: {filePath}");

        var currentText = SourceText.From(await File.ReadAllTextAsync(filePath));
        var updatedSolution = solution.WithDocumentText(doc.Id, currentText);
        if (!entry.Workspace.TryApplyChanges(updatedSolution))
            throw new InvalidOperationException($"Workspace.TryApplyChanges failed for {filePath}");

        var freshDoc = entry.Workspace.CurrentSolution
            .GetProject(entry.ProjectId)!
            .Documents.First(d => d.FilePath == filePath);
        var compilation = await freshDoc.Project.GetCompilationAsync()
            ?? throw new InvalidOperationException("Compilation failed");

        return await AnalyzeDocumentAsync(freshDoc, compilation);
    }

    public void InvalidateProject(string projectPath) => _cache.TryRemove(projectPath, out _);

    // ── Workspace cache ───────────────────────────────────────────────

    private Task<ProjectCacheEntry> GetOrLoadAsync(string projectPath) =>
        _cache.GetOrAdd(projectPath, p => new Lazy<Task<ProjectCacheEntry>>(async () =>
        {
            var workspace = MSBuildWorkspace.Create();
            var project = await workspace.OpenProjectAsync(p);
            return new ProjectCacheEntry(workspace, project.Id);
        })).Value;

    // ── Document analysis ─────────────────────────────────────────────

    private async Task<AnalysisResult> AnalyzeDocumentAsync(Document doc, Compilation compilation)
    {
        var tree = await doc.GetSyntaxTreeAsync()
            ?? throw new InvalidOperationException($"No syntax tree for {doc.FilePath}");
        var root = await tree.GetRootAsync();
        var model = compilation.GetSemanticModel(tree);
        return new AnalysisResult
        {
            Entities = ExtractEntities(root, model, doc.FilePath ?? ""),
            Relations = ExtractRelations(root, model),
        };
    }

    private List<EntityDto> ExtractEntities(SyntaxNode root, SemanticModel model, string filePath)
    {
        var entities = new List<EntityDto>();

        foreach (var decl in root.DescendantNodes().OfType<TypeDeclarationSyntax>())
        {
            if (model.GetDeclaredSymbol(decl) is not INamedTypeSymbol sym) continue;
            entities.Add(new EntityDto
            {
                QualifiedName = QN(sym),
                Name = sym.Name,
                Type = sym.TypeKind == TypeKind.Interface ? "interface" : "class",
                Namespace = sym.ContainingNamespace.ToDisplayString(),
                FilePath = filePath,
                LineStart = LineOf(decl.GetLocation(), start: true),
                LineEnd = LineOf(decl.GetLocation(), start: false),
                Signature = $"{decl.Modifiers} {decl.Keyword} {sym.Name}".Trim(),
                Content = decl.GetText().ToString(),
                IsAbstract = sym.IsAbstract,
                IsInterface = sym.TypeKind == TypeKind.Interface,
                Visibility = sym.DeclaredAccessibility.ToString().ToLowerInvariant(),
            });
        }

        foreach (var decl in root.DescendantNodes().OfType<MethodDeclarationSyntax>())
        {
            if (model.GetDeclaredSymbol(decl) is not IMethodSymbol sym) continue;
            entities.Add(new EntityDto
            {
                QualifiedName = QN(sym),
                Name = sym.Name,
                Type = "method",
                ClassName = sym.ContainingType.Name,
                Namespace = sym.ContainingNamespace.ToDisplayString(),
                FilePath = filePath,
                LineStart = LineOf(decl.GetLocation(), start: true),
                LineEnd = LineOf(decl.GetLocation(), start: false),
                Signature = sym.ToDisplayString(),
                Content = decl.GetText().ToString(),
                Visibility = sym.DeclaredAccessibility.ToString().ToLowerInvariant(),
                IsAsync = sym.IsAsync,
                ReturnType = sym.ReturnType.ToDisplayString(),
                Docstring = GetXmlDoc(sym),
            });
        }

        return entities;
    }

    private List<RelationDto> ExtractRelations(SyntaxNode root, SemanticModel model)
    {
        var relations = new List<RelationDto>();

        foreach (var inv in root.DescendantNodes().OfType<InvocationExpressionSyntax>())
        {
            var callerDecl = inv.Ancestors().OfType<MethodDeclarationSyntax>().FirstOrDefault();
            if (callerDecl == null) continue;
            if (model.GetDeclaredSymbol(callerDecl) is not IMethodSymbol callerSym) continue;
            if (model.GetSymbolInfo(inv).Symbol is not IMethodSymbol calleeSym) continue;

            // Skip purely external calls (BCL, public NuGet) but keep internal namespaces.
            if (calleeSym.Locations.All(l => l.IsInMetadata)
                && !IsInternalNamespace(calleeSym.ContainingNamespace.ToDisplayString()))
                continue;

            relations.Add(new RelationDto
            {
                From = QN(callerSym),
                To = QN(calleeSym),
                Type = "CALLS",
                Confidence = 1.0f,
                ResolutionType = "semantic",
            });
        }

        foreach (var decl in root.DescendantNodes().OfType<ClassDeclarationSyntax>())
        {
            if (model.GetDeclaredSymbol(decl) is not INamedTypeSymbol sym) continue;

            if (sym.BaseType is { } baseType
                && baseType.SpecialType != SpecialType.System_Object
                && baseType.Locations.Any(l => !l.IsInMetadata))
            {
                relations.Add(new RelationDto
                {
                    From = QN(sym),
                    To = QN(baseType),
                    Type = "EXTENDS",
                    Confidence = 1.0f,
                    ResolutionType = "semantic",
                });
            }

            foreach (var iface in sym.Interfaces.Where(i => i.Locations.Any(l => !l.IsInMetadata)))
            {
                relations.Add(new RelationDto
                {
                    From = QN(sym),
                    To = QN(iface),
                    Type = "IMPLEMENTS",
                    Confidence = 1.0f,
                    ResolutionType = "semantic",
                });
            }
        }

        return relations;
    }

    // ── Helpers ───────────────────────────────────────────────────────

    private bool IsInternalNamespace(string ns) =>
        _internalNamespaces.Any(prefix => ns.StartsWith(prefix, StringComparison.Ordinal));

    private static string QN(IMethodSymbol s) =>
        $"{s.ContainingNamespace.ToDisplayString()}::{s.ContainingType.Name}.{s.Name}";

    private static string QN(INamedTypeSymbol s) =>
        $"{s.ContainingNamespace.ToDisplayString()}::{s.Name}";

    private static int LineOf(Location loc, bool start) =>
        (start
            ? loc.GetLineSpan().StartLinePosition.Line
            : loc.GetLineSpan().EndLinePosition.Line) + 1;

    private static string? GetXmlDoc(ISymbol symbol)
    {
        var xml = symbol.GetDocumentationCommentXml()?.Trim();
        return string.IsNullOrEmpty(xml) ? null : xml;
    }
}
