#include "UnrealCodexGraphLibrary.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "K2Node_CallFunction.h"
#include "K2Node_CommutativeAssociativeBinaryOperator.h"
#include "K2Node_DynamicCast.h"
#include "K2Node_Event.h"
#include "K2Node_ExecutionSequence.h"
#include "K2Node_IfThenElse.h"
#include "K2Node_Knot.h"
#include "K2Node_MakeStruct.h"
#include "K2Node_BreakStruct.h"
#include "K2Node_Self.h"
#include "K2Node_VariableGet.h"
#include "K2Node_VariableSet.h"
#include "K2Node_CustomEvent.h"
#include "UObject/UnrealType.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Misc/PackageName.h"
#include "ScopedTransaction.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

namespace UnrealCodexGraph
{
FString ToJson(const TSharedRef<FJsonObject>& Object)
{
    FString Output;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Output);
    FJsonSerializer::Serialize(Object, Writer);
    return Output;
}

FString Error(const FString& Message)
{
    const TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetBoolField(TEXT("success"), false);
    Result->SetStringField(TEXT("error"), Message);
    return ToJson(Result);
}

UBlueprint* LoadBlueprint(const FString& BlueprintPath, FString& OutError)
{
    FString PackagePath = BlueprintPath;
    FString ObjectName;
    if (BlueprintPath.Split(TEXT("."), &PackagePath, &ObjectName))
    {
        // LoadObject accepts the complete object path below.
    }
    else
    {
        ObjectName = FPackageName::GetLongPackageAssetName(BlueprintPath);
    }

    const FString ObjectPath = BlueprintPath.Contains(TEXT("."))
        ? BlueprintPath
        : FString::Printf(TEXT("%s.%s"), *BlueprintPath, *ObjectName);
    UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *ObjectPath);
    if (!Blueprint)
    {
        OutError = FString::Printf(TEXT("Blueprint not found: %s"), *BlueprintPath);
    }
    return Blueprint;
}

UEdGraph* FindGraph(UBlueprint* Blueprint, const FString& GraphName, FString& OutError)
{
    TArray<UEdGraph*> Graphs;
    Graphs.Append(Blueprint->UbergraphPages);
    Graphs.Append(Blueprint->FunctionGraphs);
    Graphs.Append(Blueprint->MacroGraphs);
    Graphs.Append(Blueprint->DelegateSignatureGraphs);
    for (UEdGraph* Graph : Graphs)
    {
        if (Graph && Graph->GetName().Equals(GraphName, ESearchCase::IgnoreCase))
        {
            return Graph;
        }
    }
    OutError = FString::Printf(TEXT("Graph not found: %s"), *GraphName);
    return nullptr;
}

UClass* LoadOwnerClass(const FString& OwnerClassPath, FString& OutError)
{
    UClass* OwnerClass = LoadObject<UClass>(nullptr, *OwnerClassPath);
    if (!OwnerClass)
    {
        OutError = FString::Printf(TEXT("Class not found: %s"), *OwnerClassPath);
    }
    return OwnerClass;
}

UFunction* FindFunction(UClass* OwnerClass, const FString& FunctionName, FString& OutError)
{
    UFunction* Function = OwnerClass->FindFunctionByName(FName(*FunctionName));
    if (!Function)
    {
        OutError = FString::Printf(
            TEXT("Function not found: %s.%s"),
            *OwnerClass->GetPathName(),
            *FunctionName
        );
    }
    return Function;
}

UEdGraphNode* FindNode(UEdGraph* Graph, const FString& GuidText, FString& OutError)
{
    FGuid Guid;
    if (!FGuid::Parse(GuidText, Guid))
    {
        OutError = FString::Printf(TEXT("Invalid node GUID: %s"), *GuidText);
        return nullptr;
    }
    for (UEdGraphNode* Node : Graph->Nodes)
    {
        if (Node && Node->NodeGuid == Guid)
        {
            return Node;
        }
    }
    OutError = FString::Printf(TEXT("Node not found: %s"), *GuidText);
    return nullptr;
}

FString NormalizePinName(const FString& Name)
{
    FString Result = Name;
    Result.ReplaceInline(TEXT(" "), TEXT(""));
    Result.ReplaceInline(TEXT("_"), TEXT(""));
    return Result.ToLower();
}

UEdGraphPin* FindPin(UEdGraphNode* Node, const FString& PinName, EEdGraphPinDirection Direction, FString& OutError)
{
    const FString Wanted = NormalizePinName(PinName);
    for (UEdGraphPin* Pin : Node->Pins)
    {
        if (Pin && Pin->Direction == Direction && NormalizePinName(Pin->PinName.ToString()) == Wanted)
        {
            return Pin;
        }
    }
    OutError = FString::Printf(
        TEXT("Pin not found on node %s: %s"),
        *Node->NodeGuid.ToString(),
        *PinName
    );
    return nullptr;
}

TSharedRef<FJsonObject> NodeResult(UEdGraphNode* Node)
{
    const TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetBoolField(TEXT("success"), true);
    Result->SetStringField(TEXT("node_guid"), Node->NodeGuid.ToString());
    Result->SetStringField(TEXT("node_class"), Node->GetClass()->GetPathName());
    Result->SetStringField(TEXT("title"), Node->GetNodeTitle(ENodeTitleType::ListView).ToString());
    return Result;
}

template <typename NodeType>
NodeType* CreateNode(UEdGraph* Graph, const FVector2D& Position)
{
    FGraphNodeCreator<NodeType> Creator(*Graph);
    NodeType* Node = Creator.CreateNode();
    Node->NodePosX = FMath::RoundToInt(Position.X);
    Node->NodePosY = FMath::RoundToInt(Position.Y);
    Creator.Finalize();
    return Node;
}

TSharedPtr<FJsonObject> ParseParameters(const FString& ParametersJson, FString& OutError)
{
    const TSharedPtr<FJsonObject> Parameters = MakeShared<FJsonObject>();
    if (ParametersJson.IsEmpty())
    {
        return Parameters;
    }
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(ParametersJson);
    TSharedPtr<FJsonObject> Parsed;
    if (!FJsonSerializer::Deserialize(Reader, Parsed) || !Parsed.IsValid())
    {
        OutError = TEXT("Node parameters must be a valid JSON object");
        return nullptr;
    }
    return Parsed;
}

bool ValidateVariableAccess(
    UBlueprint* Blueprint,
    const FString& VariableName,
    bool bForSet,
    FString& OutError
)
{
    if (!Blueprint || !Blueprint->GeneratedClass)
    {
        OutError = TEXT("Blueprint generated class is unavailable for variable validation");
        return false;
    }
    const FProperty* Property = FindFProperty<FProperty>(
        Blueprint->GeneratedClass,
        FName(*VariableName)
    );
    if (!Property)
    {
        OutError = FString::Printf(TEXT("Blueprint variable not found: %s"), *VariableName);
        return false;
    }
    const EPropertyFlags RequiredFlags = CPF_BlueprintVisible;
    if (!Property->HasAnyPropertyFlags(RequiredFlags))
    {
        OutError = FString::Printf(TEXT("Blueprint variable is not Blueprint-visible: %s"), *VariableName);
        return false;
    }
    if (bForSet && Property->HasAnyPropertyFlags(CPF_BlueprintReadOnly | CPF_EditConst | CPF_Transient))
    {
        OutError = FString::Printf(TEXT("Blueprint variable is read-only or transient: %s"), *VariableName);
        return false;
    }
    return true;
}
}

namespace
{
TSharedRef<FJsonObject> CatalogError(const FString& Code, const FString& Message)
{
    const TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetBoolField(TEXT("success"), false);
    Result->SetStringField(TEXT("error_code"), Code);
    Result->SetStringField(TEXT("error"), Message);
    return Result;
}

TSharedRef<FJsonObject> MakeNodeSpec(const FString& Kind, const FString& Title, const FString& Description)
{
    const TSharedRef<FJsonObject> Spec = MakeShared<FJsonObject>();
    Spec->SetStringField(TEXT("kind"), Kind);
    Spec->SetStringField(TEXT("title"), Title);
    Spec->SetStringField(TEXT("description"), Description);
    Spec->SetBoolField(TEXT("available"), true);
    TArray<TSharedPtr<FJsonValue>> Parameters;
    if (Kind == TEXT("function_call") || Kind == TEXT("event") || Kind == TEXT("operator"))
    {
        Parameters.Add(MakeShared<FJsonValueString>(TEXT("owner_class")));
        Parameters.Add(MakeShared<FJsonValueString>(TEXT("function")));
    }
    else if (Kind == TEXT("custom_event"))
    {
        Parameters.Add(MakeShared<FJsonValueString>(TEXT("event_name")));
    }
    else if (Kind == TEXT("variable_get") || Kind == TEXT("variable_set"))
    {
        Parameters.Add(MakeShared<FJsonValueString>(TEXT("variable_name")));
    }
    else if (Kind == TEXT("dynamic_cast"))
    {
        Parameters.Add(MakeShared<FJsonValueString>(TEXT("target_class")));
    }
    else if (Kind == TEXT("struct_make") || Kind == TEXT("struct_break"))
    {
        Parameters.Add(MakeShared<FJsonValueString>(TEXT("struct_path")));
    }
    Spec->SetArrayField(TEXT("parameters"), Parameters);
    return Spec;
}

TArray<TSharedRef<FJsonObject>> CatalogNodeSpecs()
{
    return {
        MakeNodeSpec(TEXT("function_call"), TEXT("Function Call"), TEXT("Call a reflected Blueprint function")),
        MakeNodeSpec(TEXT("event"), TEXT("Event"), TEXT("Add an event entry node")),
        MakeNodeSpec(TEXT("operator"), TEXT("Integer Add Operator"), TEXT("Add the constrained Kismet integer addition operator")),
        MakeNodeSpec(TEXT("custom_event"), TEXT("Custom Event"), TEXT("Add a named custom event entry node")),
        MakeNodeSpec(TEXT("branch"), TEXT("Branch"), TEXT("Conditional execution branch")),
        MakeNodeSpec(TEXT("sequence"), TEXT("Sequence"), TEXT("Sequential execution outputs")),
        MakeNodeSpec(TEXT("reroute"), TEXT("Reroute"), TEXT("Execution or data wire reroute")),
        MakeNodeSpec(TEXT("self"), TEXT("Self"), TEXT("Reference to the Blueprint self object")),
        MakeNodeSpec(TEXT("variable_get"), TEXT("Get Variable"), TEXT("Read a Blueprint-visible variable")),
        MakeNodeSpec(TEXT("variable_set"), TEXT("Set Variable"), TEXT("Write a writable Blueprint-visible variable")),
        MakeNodeSpec(TEXT("dynamic_cast"), TEXT("Cast To"), TEXT("Cast an object to a target class")),
        MakeNodeSpec(TEXT("struct_make"), TEXT("Make Struct"), TEXT("Construct a reflected struct value")),
        MakeNodeSpec(TEXT("struct_break"), TEXT("Break Struct"), TEXT("Extract reflected struct members"))
    };
}
}

FString UUnrealCodexGraphLibrary::GetCapabilities()
{
    const TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetBoolField(TEXT("success"), true);
    Result->SetStringField(TEXT("bridge"), TEXT("UnrealCodexGraph"));
    Result->SetStringField(TEXT("bridge_version"), TEXT("0.1.0"));
    Result->SetStringField(TEXT("engine_target"), TEXT("5.8"));
    Result->SetBoolField(TEXT("function_call_nodes"), true);
    Result->SetBoolField(TEXT("event_nodes"), true);
    Result->SetBoolField(TEXT("custom_event_nodes"), true);
    Result->SetBoolField(TEXT("pin_connections"), true);
    Result->SetBoolField(TEXT("pin_default_values"), true);
    Result->SetBoolField(TEXT("graph_inspection"), true);
    TArray<TSharedPtr<FJsonValue>> NodeKinds;
    for (const TCHAR* Kind : {
        TEXT("function_call"), TEXT("event"), TEXT("operator"), TEXT("custom_event"), TEXT("branch"), TEXT("sequence"),
        TEXT("reroute"), TEXT("self"), TEXT("variable_get"), TEXT("variable_set"),
        TEXT("dynamic_cast"), TEXT("struct_make"), TEXT("struct_break")
    })
    {
        NodeKinds.Add(MakeShared<FJsonValueString>(Kind));
    }
    Result->SetArrayField(TEXT("node_kinds"), NodeKinds);
    return UnrealCodexGraph::ToJson(Result);
}

FString UUnrealCodexGraphLibrary::AddNode(
    const FString& BlueprintPath,
    const FString& GraphName,
    const FString& NodeKind,
    const FString& ParametersJson,
    const FVector2D& Position
)
{
    FString ErrorMessage;
    const TSharedPtr<FJsonObject> Parameters = UnrealCodexGraph::ParseParameters(ParametersJson, ErrorMessage);
    if (!Parameters.IsValid())
    {
        return UnrealCodexGraph::Error(ErrorMessage);
    }

    if (NodeKind.Equals(TEXT("function_call"), ESearchCase::IgnoreCase))
    {
        FString OwnerClass;
        FString Function;
        if (!Parameters->TryGetStringField(TEXT("owner_class"), OwnerClass) ||
            !Parameters->TryGetStringField(TEXT("function"), Function))
        {
            return UnrealCodexGraph::Error(TEXT("function_call requires owner_class and function"));
        }
        return AddFunctionCallNode(BlueprintPath, GraphName, OwnerClass, Function, Position);
    }
    if (NodeKind.Equals(TEXT("event"), ESearchCase::IgnoreCase))
    {
        FString OwnerClass;
        FString Function;
        if (!Parameters->TryGetStringField(TEXT("owner_class"), OwnerClass) ||
            !Parameters->TryGetStringField(TEXT("function"), Function))
        {
            return UnrealCodexGraph::Error(TEXT("event requires owner_class and function"));
        }
        return AddEventNode(BlueprintPath, GraphName, OwnerClass, Function, Position);
    }
    if (NodeKind.Equals(TEXT("custom_event"), ESearchCase::IgnoreCase))
    {
        FString EventName;
        if (!Parameters->TryGetStringField(TEXT("event_name"), EventName) || EventName.IsEmpty())
        {
            return UnrealCodexGraph::Error(TEXT("custom_event requires event_name"));
        }
        UBlueprint* Blueprint = UnrealCodexGraph::LoadBlueprint(BlueprintPath, ErrorMessage);
        UEdGraph* Graph = Blueprint ? UnrealCodexGraph::FindGraph(Blueprint, GraphName, ErrorMessage) : nullptr;
        if (!Graph)
        {
            return UnrealCodexGraph::Error(ErrorMessage);
        }
        if (!FBlueprintEditorUtils::IsEventGraph(Graph))
        {
            return UnrealCodexGraph::Error(TEXT("custom_event requires an event graph"));
        }
        if (FBlueprintEditorUtils::FindCustomEventNode(Blueprint, FName(*EventName)))
        {
            return UnrealCodexGraph::Error(TEXT("custom_event name already exists"));
        }
        const FScopedTransaction Transaction(NSLOCTEXT("UnrealCodexGraph", "AddCustomEvent", "Add Blueprint custom event"));
        Blueprint->Modify();
        Graph->Modify();
        UK2Node_CustomEvent* Node = NewObject<UK2Node_CustomEvent>(Graph);
        Node->CreateNewGuid();

        Node->CustomFunctionName = FName(*EventName);
        Node->bIsEditable = true;
        Node->SetFlags(RF_Transactional);
        Node->NodePosX = FMath::RoundToInt(Position.X);
        Node->NodePosY = FMath::RoundToInt(Position.Y);
        Node->AllocateDefaultPins();
        Node->PostPlacedNewNode();
        Graph->AddNode(Node, true, false);
        FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
        return UnrealCodexGraph::ToJson(UnrealCodexGraph::NodeResult(Node));
    }

    UBlueprint* Blueprint = UnrealCodexGraph::LoadBlueprint(BlueprintPath, ErrorMessage);
    UEdGraph* Graph = Blueprint ? UnrealCodexGraph::FindGraph(Blueprint, GraphName, ErrorMessage) : nullptr;
    if (!Graph)
    {
        return UnrealCodexGraph::Error(ErrorMessage);
    }

    if (NodeKind.Equals(TEXT("variable_get"), ESearchCase::IgnoreCase) ||
        NodeKind.Equals(TEXT("variable_set"), ESearchCase::IgnoreCase))
    {
        FString VariableName;
        if (!Parameters->TryGetStringField(TEXT("variable_name"), VariableName) || VariableName.IsEmpty())
        {
            return UnrealCodexGraph::Error(TEXT("Variable nodes require variable_name"));
        }
        const bool bForSet = NodeKind.Equals(TEXT("variable_set"), ESearchCase::IgnoreCase);
        if (!UnrealCodexGraph::ValidateVariableAccess(Blueprint, VariableName, bForSet, ErrorMessage))
        {
            return UnrealCodexGraph::Error(ErrorMessage);
        }
    }

    if (NodeKind.Equals(TEXT("operator"), ESearchCase::IgnoreCase))
    {
        FString OwnerClassPath;
        FString FunctionName;
        if (!Parameters->TryGetStringField(TEXT("owner_class"), OwnerClassPath) ||
            !Parameters->TryGetStringField(TEXT("function"), FunctionName))
        {
            return UnrealCodexGraph::Error(TEXT("operator requires owner_class and function"));
        }
        if (OwnerClassPath != TEXT("/Script/Engine.KismetMathLibrary") || FunctionName != TEXT("Add_IntInt"))
        {
            return UnrealCodexGraph::Error(TEXT("Unsupported operator: only /Script/Engine.KismetMathLibrary.Add_IntInt is available"));
        }
        UClass* OperatorClass = LoadObject<UClass>(nullptr, *OwnerClassPath);
        if (!OperatorClass || OperatorClass->GetPathName() != OwnerClassPath)
        {
            return UnrealCodexGraph::Error(TEXT("Operator owner class not found: /Script/Engine.KismetMathLibrary"));
        }
        if (!OperatorClass->FindFunctionByName(FName(*FunctionName)))
        {
            return UnrealCodexGraph::Error(TEXT("Operator function not found: /Script/Engine.KismetMathLibrary.Add_IntInt"));
        }
    }

    if (!NodeKind.Equals(TEXT("custom_event"), ESearchCase::IgnoreCase) &&
        !NodeKind.Equals(TEXT("branch"), ESearchCase::IgnoreCase) &&
        !NodeKind.Equals(TEXT("sequence"), ESearchCase::IgnoreCase) &&
        !NodeKind.Equals(TEXT("reroute"), ESearchCase::IgnoreCase) &&
        !NodeKind.Equals(TEXT("self"), ESearchCase::IgnoreCase) &&
        !NodeKind.Equals(TEXT("variable_get"), ESearchCase::IgnoreCase) &&
        !NodeKind.Equals(TEXT("variable_set"), ESearchCase::IgnoreCase) &&
        !NodeKind.Equals(TEXT("dynamic_cast"), ESearchCase::IgnoreCase) &&
        !NodeKind.Equals(TEXT("struct_make"), ESearchCase::IgnoreCase) &&
        !NodeKind.Equals(TEXT("struct_break"), ESearchCase::IgnoreCase) &&
        !NodeKind.Equals(TEXT("operator"), ESearchCase::IgnoreCase))
    {
        return UnrealCodexGraph::Error(FString::Printf(TEXT("Unsupported node kind: %s"), *NodeKind));
    }
    FString StructPath;
    UScriptStruct* StructType = nullptr;
    if (NodeKind.Equals(TEXT("struct_make"), ESearchCase::IgnoreCase) ||
        NodeKind.Equals(TEXT("struct_break"), ESearchCase::IgnoreCase))
    {
        if (!Parameters->TryGetStringField(TEXT("struct_path"), StructPath) || StructPath.IsEmpty())
        {
            return UnrealCodexGraph::Error(TEXT("Struct nodes require struct_path"));
        }
        StructType = LoadObject<UScriptStruct>(nullptr, *StructPath);
        if (!StructType)
        {
            return UnrealCodexGraph::Error(FString::Printf(TEXT("Struct not found: %s"), *StructPath));
        }
    }
    if (NodeKind.Equals(TEXT("dynamic_cast"), ESearchCase::IgnoreCase))
    {
        FString TargetClassPath;
        if (!Parameters->TryGetStringField(TEXT("target_class"), TargetClassPath))
        {
            return UnrealCodexGraph::Error(TEXT("dynamic_cast requires target_class"));
        }
        if (!UnrealCodexGraph::LoadOwnerClass(TargetClassPath, ErrorMessage))
        {
            return UnrealCodexGraph::Error(ErrorMessage);
        }
    }
    const FScopedTransaction Transaction(NSLOCTEXT("UnrealCodexGraph", "AddNode", "Add Blueprint graph node"));
    Blueprint->Modify();
    Graph->Modify();
    UEdGraphNode* Node = nullptr;
    if (NodeKind.Equals(TEXT("operator"), ESearchCase::IgnoreCase))
    {
        UClass* OperatorClass = LoadObject<UClass>(nullptr, TEXT("/Script/Engine.KismetMathLibrary"));
        UFunction* OperatorFunction = OperatorClass ? OperatorClass->FindFunctionByName(FName(TEXT("Add_IntInt"))) : nullptr;
        FGraphNodeCreator<UK2Node_CommutativeAssociativeBinaryOperator> Creator(*Graph);
        UK2Node_CommutativeAssociativeBinaryOperator* OperatorNode = Creator.CreateNode();
        OperatorNode->SetFromFunction(OperatorFunction);
        OperatorNode->NodePosX = FMath::RoundToInt(Position.X);
        OperatorNode->NodePosY = FMath::RoundToInt(Position.Y);
        Creator.Finalize();
        Node = OperatorNode;
    }
    else if (NodeKind.Equals(TEXT("branch"), ESearchCase::IgnoreCase))
    {
        Node = UnrealCodexGraph::CreateNode<UK2Node_IfThenElse>(Graph, Position);
    }

    else if (NodeKind.Equals(TEXT("sequence"), ESearchCase::IgnoreCase))
    {
        Node = UnrealCodexGraph::CreateNode<UK2Node_ExecutionSequence>(Graph, Position);
    }
    else if (NodeKind.Equals(TEXT("reroute"), ESearchCase::IgnoreCase))
    {
        Node = UnrealCodexGraph::CreateNode<UK2Node_Knot>(Graph, Position);
    }
    else if (NodeKind.Equals(TEXT("self"), ESearchCase::IgnoreCase))
    {
        Node = UnrealCodexGraph::CreateNode<UK2Node_Self>(Graph, Position);
    }
    else if (NodeKind.Equals(TEXT("variable_get"), ESearchCase::IgnoreCase) ||
             NodeKind.Equals(TEXT("variable_set"), ESearchCase::IgnoreCase))
    {
        FString VariableName;
        Parameters->TryGetStringField(TEXT("variable_name"), VariableName);
        if (NodeKind.Equals(TEXT("variable_get"), ESearchCase::IgnoreCase))
        {
            FGraphNodeCreator<UK2Node_VariableGet> Creator(*Graph);
            UK2Node_VariableGet* VariableNode = Creator.CreateNode();
            VariableNode->VariableReference.SetSelfMember(FName(*VariableName));
            VariableNode->NodePosX = FMath::RoundToInt(Position.X);
            VariableNode->NodePosY = FMath::RoundToInt(Position.Y);
            Creator.Finalize();
            Node = VariableNode;
        }
        else
        {
            FGraphNodeCreator<UK2Node_VariableSet> Creator(*Graph);
            UK2Node_VariableSet* VariableNode = Creator.CreateNode();
            VariableNode->VariableReference.SetSelfMember(FName(*VariableName));
            VariableNode->NodePosX = FMath::RoundToInt(Position.X);
            VariableNode->NodePosY = FMath::RoundToInt(Position.Y);
            Creator.Finalize();
            Node = VariableNode;
        }
    }
    else if (NodeKind.Equals(TEXT("dynamic_cast"), ESearchCase::IgnoreCase))
    {
        FString TargetClassPath;
        Parameters->TryGetStringField(TEXT("target_class"), TargetClassPath);
        UClass* TargetClass = UnrealCodexGraph::LoadOwnerClass(TargetClassPath, ErrorMessage);
        FGraphNodeCreator<UK2Node_DynamicCast> Creator(*Graph);
        UK2Node_DynamicCast* CastNode = Creator.CreateNode();
        CastNode->TargetType = TargetClass;
        CastNode->NodePosX = FMath::RoundToInt(Position.X);
        CastNode->NodePosY = FMath::RoundToInt(Position.Y);
        Creator.Finalize();
        Node = CastNode;
    }
    else if (NodeKind.Equals(TEXT("struct_make"), ESearchCase::IgnoreCase))
    {
        FGraphNodeCreator<UK2Node_MakeStruct> Creator(*Graph);
        UK2Node_MakeStruct* StructNode = Creator.CreateNode();
        StructNode->StructType = StructType;
        StructNode->NodePosX = FMath::RoundToInt(Position.X);
        StructNode->NodePosY = FMath::RoundToInt(Position.Y);
        Creator.Finalize();
        Node = StructNode;
    }
    else if (NodeKind.Equals(TEXT("struct_break"), ESearchCase::IgnoreCase))
    {
        FGraphNodeCreator<UK2Node_BreakStruct> Creator(*Graph);
        UK2Node_BreakStruct* StructNode = Creator.CreateNode();
        StructNode->StructType = StructType;
        StructNode->NodePosX = FMath::RoundToInt(Position.X);
        StructNode->NodePosY = FMath::RoundToInt(Position.Y);
        Creator.Finalize();
        Node = StructNode;
    }

    if (!Node)
    {
        return UnrealCodexGraph::Error(TEXT("Unreal could not create the requested node"));
    }
    FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
    return UnrealCodexGraph::ToJson(UnrealCodexGraph::NodeResult(Node));

}

FString UUnrealCodexGraphLibrary::InspectGraph(const FString& BlueprintPath, const FString& GraphName)
{
    FString ErrorMessage;
    UBlueprint* Blueprint = UnrealCodexGraph::LoadBlueprint(BlueprintPath, ErrorMessage);
    if (!Blueprint)
    {
        return UnrealCodexGraph::Error(ErrorMessage);
    }
    UEdGraph* Graph = UnrealCodexGraph::FindGraph(Blueprint, GraphName, ErrorMessage);
    if (!Graph)
    {
        return UnrealCodexGraph::Error(ErrorMessage);
    }

    TArray<TSharedPtr<FJsonValue>> Nodes;
    for (UEdGraphNode* Node : Graph->Nodes)
    {
        if (!Node)
        {
            continue;
        }
        const TSharedRef<FJsonObject> NodeJson = MakeShared<FJsonObject>();
        NodeJson->SetStringField(TEXT("guid"), Node->NodeGuid.ToString());
        NodeJson->SetStringField(TEXT("class"), Node->GetClass()->GetPathName());
        NodeJson->SetStringField(TEXT("title"), Node->GetNodeTitle(ENodeTitleType::ListView).ToString());
        NodeJson->SetNumberField(TEXT("x"), Node->NodePosX);
        NodeJson->SetNumberField(TEXT("y"), Node->NodePosY);
        TArray<TSharedPtr<FJsonValue>> Pins;
        for (UEdGraphPin* Pin : Node->Pins)
        {
            if (!Pin)
            {
                continue;
            }
            const TSharedRef<FJsonObject> PinJson = MakeShared<FJsonObject>();
            PinJson->SetStringField(TEXT("name"), Pin->PinName.ToString());
            PinJson->SetStringField(
                TEXT("direction"),
                Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output")
            );
            PinJson->SetStringField(TEXT("category"), Pin->PinType.PinCategory.ToString());
            PinJson->SetStringField(TEXT("subcategory"), Pin->PinType.PinSubCategory.ToString());
            PinJson->SetStringField(TEXT("default_value"), Pin->DefaultValue);
            TArray<TSharedPtr<FJsonValue>> Linked;
            for (UEdGraphPin* LinkedPin : Pin->LinkedTo)
            {
                if (LinkedPin && LinkedPin->GetOwningNode())
                {
                    Linked.Add(MakeShared<FJsonValueString>(
                        LinkedPin->GetOwningNode()->NodeGuid.ToString() + TEXT(":") + LinkedPin->PinName.ToString()
                    ));
                }
            }
            PinJson->SetArrayField(TEXT("linked_to"), Linked);
            Pins.Add(MakeShared<FJsonValueObject>(PinJson));
        }
        NodeJson->SetArrayField(TEXT("pins"), Pins);
        Nodes.Add(MakeShared<FJsonValueObject>(NodeJson));
    }


    const TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetBoolField(TEXT("success"), true);
    Result->SetStringField(TEXT("blueprint"), BlueprintPath);
    Result->SetStringField(TEXT("graph"), GraphName);
    Result->SetArrayField(TEXT("nodes"), Nodes);
    return UnrealCodexGraph::ToJson(Result);
}
FString UUnrealCodexGraphLibrary::DescribeGraph(const FString& BlueprintPath, const FString& GraphName)
{
    FString ErrorMessage;
    UBlueprint* Blueprint = UnrealCodexGraph::LoadBlueprint(BlueprintPath, ErrorMessage);
    if (!Blueprint)
    {
        return UnrealCodexGraph::ToJson(CatalogError(TEXT("blueprint_not_found"), ErrorMessage));
    }
    UEdGraph* Graph = UnrealCodexGraph::FindGraph(Blueprint, GraphName, ErrorMessage);
    if (!Graph)
    {
        return UnrealCodexGraph::ToJson(CatalogError(TEXT("graph_not_found"), ErrorMessage));
    }
    const TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetBoolField(TEXT("success"), true);
    Result->SetStringField(TEXT("blueprint"), BlueprintPath);
    Result->SetStringField(TEXT("graph"), Graph->GetName());
    Result->SetNumberField(TEXT("node_count"), Graph->Nodes.Num());
    Result->SetStringField(TEXT("parent_class"), Blueprint->ParentClass ? Blueprint->ParentClass->GetPathName() : FString());
    Result->SetStringField(TEXT("generated_class"), Blueprint->GeneratedClass ? Blueprint->GeneratedClass->GetPathName() : FString());
    return UnrealCodexGraph::ToJson(Result);
}
FString UUnrealCodexGraphLibrary::SearchNodeSpecs(const FString& BlueprintPath, const FString& GraphName, const FString& Query)
{
    FString ErrorMessage;
    UBlueprint* Blueprint = UnrealCodexGraph::LoadBlueprint(BlueprintPath, ErrorMessage);
    if (!Blueprint)
    {
        return UnrealCodexGraph::ToJson(CatalogError(TEXT("blueprint_not_found"), ErrorMessage));
    }
    if (!UnrealCodexGraph::FindGraph(Blueprint, GraphName, ErrorMessage))
    {
        return UnrealCodexGraph::ToJson(CatalogError(TEXT("graph_not_found"), ErrorMessage));
    }
    TArray<TSharedPtr<FJsonValue>> Matches;
    for (const TSharedRef<FJsonObject>& Spec : CatalogNodeSpecs())
    {
        const FString Haystack = Spec->GetStringField(TEXT("kind")) + TEXT(" ") + Spec->GetStringField(TEXT("title"));
        if (Query.IsEmpty() || Haystack.Contains(Query, ESearchCase::IgnoreCase))
        {
            Matches.Add(MakeShared<FJsonValueObject>(Spec));
        }
    }
    const TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetBoolField(TEXT("success"), true);
    Result->SetStringField(TEXT("blueprint"), BlueprintPath);
    Result->SetStringField(TEXT("graph"), GraphName);
    Result->SetStringField(TEXT("query"), Query);
    Result->SetArrayField(TEXT("matches"), Matches);
    Result->SetNumberField(TEXT("total"), Matches.Num());
    Result->SetBoolField(TEXT("ambiguous"), Matches.Num() > 1);
    return UnrealCodexGraph::ToJson(Result);
}

FString UUnrealCodexGraphLibrary::DescribeNodeSpec(const FString& NodeKind)
{
    for (const TSharedRef<FJsonObject>& Spec : CatalogNodeSpecs())
    {
        if (Spec->GetStringField(TEXT("kind")).Equals(NodeKind, ESearchCase::IgnoreCase))
        {
            const TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
            Result->SetBoolField(TEXT("success"), true);
            Result->SetObjectField(TEXT("node_spec"), Spec);
            return UnrealCodexGraph::ToJson(Result);
        }
    }
    return UnrealCodexGraph::ToJson(CatalogError(TEXT("node_kind_not_found"), FString::Printf(TEXT("Unsupported node kind: %s"), *NodeKind)));
}


FString UUnrealCodexGraphLibrary::ListGraphs(const FString& BlueprintPath)
{
    FString ErrorMessage;
    UBlueprint* Blueprint = UnrealCodexGraph::LoadBlueprint(BlueprintPath, ErrorMessage);
    if (!Blueprint)
    {
        return UnrealCodexGraph::Error(ErrorMessage);
    }

    TArray<TSharedPtr<FJsonValue>> Graphs;
    const auto AppendGraphs = [&Graphs](const TArray<TObjectPtr<UEdGraph>>& Source, const FString& Kind)
    {
        for (const UEdGraph* Graph : Source)
        {
            if (!Graph)
            {
                continue;
            }
            const TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
            Item->SetStringField(TEXT("name"), Graph->GetName());
            Item->SetStringField(TEXT("kind"), Kind);
            Graphs.Add(MakeShared<FJsonValueObject>(Item));
        }
    };
    AppendGraphs(Blueprint->UbergraphPages, TEXT("event"));
    AppendGraphs(Blueprint->FunctionGraphs, TEXT("function"));
    AppendGraphs(Blueprint->MacroGraphs, TEXT("macro"));
    AppendGraphs(Blueprint->DelegateSignatureGraphs, TEXT("delegate"));

    const TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetBoolField(TEXT("success"), true);
    Result->SetStringField(TEXT("blueprint"), BlueprintPath);
    Result->SetStringField(
        TEXT("parent_class"),
        Blueprint->ParentClass ? Blueprint->ParentClass->GetPathName() : FString()
    );
    Result->SetStringField(
        TEXT("generated_class"),
        Blueprint->GeneratedClass ? Blueprint->GeneratedClass->GetPathName() : FString()
    );
    Result->SetArrayField(TEXT("graphs"), Graphs);
    return UnrealCodexGraph::ToJson(Result);
}

FString UUnrealCodexGraphLibrary::AddFunctionCallNode(
    const FString& BlueprintPath,
    const FString& GraphName,
    const FString& OwnerClassPath,
    const FString& FunctionName,
    const FVector2D& Position
)
{
    FString ErrorMessage;
    UBlueprint* Blueprint = UnrealCodexGraph::LoadBlueprint(BlueprintPath, ErrorMessage);
    UEdGraph* Graph = Blueprint ? UnrealCodexGraph::FindGraph(Blueprint, GraphName, ErrorMessage) : nullptr;
    UClass* OwnerClass = Graph ? UnrealCodexGraph::LoadOwnerClass(OwnerClassPath, ErrorMessage) : nullptr;
    UFunction* Function = OwnerClass ? UnrealCodexGraph::FindFunction(OwnerClass, FunctionName, ErrorMessage) : nullptr;
    if (!Function)
    {
        return UnrealCodexGraph::Error(ErrorMessage);
    }

    const FScopedTransaction Transaction(NSLOCTEXT("UnrealCodexGraph", "AddFunctionCallNode", "Add Blueprint function call node"));
    Blueprint->Modify();
    Graph->Modify();
    FGraphNodeCreator<UK2Node_CallFunction> Creator(*Graph);
    UK2Node_CallFunction* Node = Creator.CreateNode();
    Node->SetFromFunction(Function);
    Node->NodePosX = FMath::RoundToInt(Position.X);
    Node->NodePosY = FMath::RoundToInt(Position.Y);
    Creator.Finalize();
    FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
    return UnrealCodexGraph::ToJson(UnrealCodexGraph::NodeResult(Node));
}

FString UUnrealCodexGraphLibrary::AddEventNode(
    const FString& BlueprintPath,
    const FString& GraphName,
    const FString& OwnerClassPath,
    const FString& FunctionName,
    const FVector2D& Position
)
{
    FString ErrorMessage;
    UBlueprint* Blueprint = UnrealCodexGraph::LoadBlueprint(BlueprintPath, ErrorMessage);
    UEdGraph* Graph = Blueprint ? UnrealCodexGraph::FindGraph(Blueprint, GraphName, ErrorMessage) : nullptr;
    UClass* OwnerClass = Graph ? UnrealCodexGraph::LoadOwnerClass(OwnerClassPath, ErrorMessage) : nullptr;
    UFunction* Function = OwnerClass ? UnrealCodexGraph::FindFunction(OwnerClass, FunctionName, ErrorMessage) : nullptr;
    if (!Function)
    {
        return UnrealCodexGraph::Error(ErrorMessage);
    }

    for (UEdGraphNode* ExistingNode : Graph->Nodes)
    {
        UK2Node_Event* ExistingEvent = Cast<UK2Node_Event>(ExistingNode);
        if (ExistingEvent && ExistingEvent->EventReference.GetMemberName() == Function->GetFName())
        {
            const TSharedRef<FJsonObject> Result = UnrealCodexGraph::NodeResult(ExistingEvent);
            Result->SetBoolField(TEXT("existing"), true);
            return UnrealCodexGraph::ToJson(Result);
        }
    }

    const FScopedTransaction Transaction(NSLOCTEXT("UnrealCodexGraph", "AddEventNode", "Add Blueprint event node"));
    Blueprint->Modify();
    Graph->Modify();
    FGraphNodeCreator<UK2Node_Event> Creator(*Graph);
    UK2Node_Event* Node = Creator.CreateNode();
    Node->EventReference.SetExternalMember(Function->GetFName(), OwnerClass);
    Node->bOverrideFunction = true;
    Node->NodePosX = FMath::RoundToInt(Position.X);
    Node->NodePosY = FMath::RoundToInt(Position.Y);
    Creator.Finalize();
    FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
    return UnrealCodexGraph::ToJson(UnrealCodexGraph::NodeResult(Node));
}

FString UUnrealCodexGraphLibrary::ConnectPins(
    const FString& BlueprintPath,
    const FString& GraphName,
    const FString& FromNodeGuid,
    const FString& FromPinName,
    const FString& ToNodeGuid,
    const FString& ToPinName
)
{
    FString ErrorMessage;
    UBlueprint* Blueprint = UnrealCodexGraph::LoadBlueprint(BlueprintPath, ErrorMessage);
    UEdGraph* Graph = Blueprint ? UnrealCodexGraph::FindGraph(Blueprint, GraphName, ErrorMessage) : nullptr;
    UEdGraphNode* FromNode = Graph ? UnrealCodexGraph::FindNode(Graph, FromNodeGuid, ErrorMessage) : nullptr;
    UEdGraphNode* ToNode = FromNode ? UnrealCodexGraph::FindNode(Graph, ToNodeGuid, ErrorMessage) : nullptr;
    UEdGraphPin* FromPin = ToNode ? UnrealCodexGraph::FindPin(FromNode, FromPinName, EGPD_Output, ErrorMessage) : nullptr;
    UEdGraphPin* ToPin = FromPin ? UnrealCodexGraph::FindPin(ToNode, ToPinName, EGPD_Input, ErrorMessage) : nullptr;
    if (!ToPin)
    {
        return UnrealCodexGraph::Error(ErrorMessage);
    }

    const UEdGraphSchema_K2* Schema = GetDefault<UEdGraphSchema_K2>();
    const FPinConnectionResponse Response = Schema->CanCreateConnection(FromPin, ToPin);
    if (Response.Response == CONNECT_RESPONSE_DISALLOW)
    {
        return UnrealCodexGraph::Error(Response.Message.ToString());
    }
    const FScopedTransaction Transaction(NSLOCTEXT("UnrealCodexGraph", "ConnectPins", "Connect Blueprint pins"));
    Blueprint->Modify();
    Graph->Modify();
    if (!Schema->TryCreateConnection(FromPin, ToPin))
    {
        return UnrealCodexGraph::Error(TEXT("Unreal rejected the pin connection"));
    }
    FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
    const TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetBoolField(TEXT("success"), true);
    Result->SetStringField(TEXT("from"), FromNodeGuid + TEXT(":") + FromPinName);
    Result->SetStringField(TEXT("to"), ToNodeGuid + TEXT(":") + ToPinName);
    return UnrealCodexGraph::ToJson(Result);
}

FString UUnrealCodexGraphLibrary::SetPinDefaultValue(
    const FString& BlueprintPath,
    const FString& GraphName,
    const FString& NodeGuid,
    const FString& PinName,
    const FString& Value
)
{
    FString ErrorMessage;
    UBlueprint* Blueprint = UnrealCodexGraph::LoadBlueprint(BlueprintPath, ErrorMessage);
    UEdGraph* Graph = Blueprint ? UnrealCodexGraph::FindGraph(Blueprint, GraphName, ErrorMessage) : nullptr;
    UEdGraphNode* Node = Graph ? UnrealCodexGraph::FindNode(Graph, NodeGuid, ErrorMessage) : nullptr;
    UEdGraphPin* Pin = Node ? UnrealCodexGraph::FindPin(Node, PinName, EGPD_Input, ErrorMessage) : nullptr;
    if (!Pin)
    {
        return UnrealCodexGraph::Error(ErrorMessage);
    }

    const UEdGraphSchema_K2* Schema = GetDefault<UEdGraphSchema_K2>();
    const FScopedTransaction Transaction(NSLOCTEXT("UnrealCodexGraph", "SetPinDefaultValue", "Set Blueprint pin default value"));
    Blueprint->Modify();
    Graph->Modify();
    Schema->TrySetDefaultValue(*Pin, Value);
    FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
    const TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetBoolField(TEXT("success"), true);
    Result->SetStringField(TEXT("node_guid"), NodeGuid);
    Result->SetStringField(TEXT("pin"), PinName);
    Result->SetStringField(TEXT("value"), Value);
    return UnrealCodexGraph::ToJson(Result);
}

FString UUnrealCodexGraphLibrary::RemoveNode(
    const FString& BlueprintPath, const FString& GraphName, const FString& NodeGuid
)
{
    FString ErrorMessage;
    UBlueprint* Blueprint = UnrealCodexGraph::LoadBlueprint(BlueprintPath, ErrorMessage);
    UEdGraph* Graph = Blueprint ? UnrealCodexGraph::FindGraph(Blueprint, GraphName, ErrorMessage) : nullptr;
    UEdGraphNode* Node = Graph ? UnrealCodexGraph::FindNode(Graph, NodeGuid, ErrorMessage) : nullptr;
    if (!Node)
    {
        return UnrealCodexGraph::Error(ErrorMessage);
    }
    const FScopedTransaction Transaction(NSLOCTEXT("UnrealCodexGraph", "RemoveNode", "Remove Blueprint graph node"));
    Blueprint->Modify();
    Graph->Modify();
    Node->Modify();
    Graph->RemoveNode(Node);
    FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
    const TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetBoolField(TEXT("success"), true);
    Result->SetStringField(TEXT("node_guid"), NodeGuid);
    return UnrealCodexGraph::ToJson(Result);
}

FString UUnrealCodexGraphLibrary::DisconnectPins(
    const FString& BlueprintPath, const FString& GraphName,
    const FString& FromNodeGuid, const FString& FromPinName,
    const FString& ToNodeGuid, const FString& ToPinName, bool bAllLinks
)
{
    FString ErrorMessage;
    UBlueprint* Blueprint = UnrealCodexGraph::LoadBlueprint(BlueprintPath, ErrorMessage);
    UEdGraph* Graph = Blueprint ? UnrealCodexGraph::FindGraph(Blueprint, GraphName, ErrorMessage) : nullptr;
    UEdGraphNode* FromNode = Graph ? UnrealCodexGraph::FindNode(Graph, FromNodeGuid, ErrorMessage) : nullptr;
    UEdGraphPin* FromPin = nullptr;
    UEdGraphPin* ToPin = nullptr;
    if (FromNode && bAllLinks)
    {
        for (UEdGraphPin* Candidate : FromNode->Pins)
        {
            if (Candidate && UnrealCodexGraph::NormalizePinName(Candidate->PinName.ToString()) ==
                UnrealCodexGraph::NormalizePinName(FromPinName))
            {
                FromPin = Candidate;
                break;
            }
        }
        if (!FromPin)
        {
            ErrorMessage = FString::Printf(TEXT("Pin not found on node %s: %s"), *FromNodeGuid, *FromPinName);
        }
    }
    else if (FromNode)
    {
        UEdGraphNode* ToNode = UnrealCodexGraph::FindNode(Graph, ToNodeGuid, ErrorMessage);
        FromPin = ToNode ? UnrealCodexGraph::FindPin(FromNode, FromPinName, EGPD_Output, ErrorMessage) : nullptr;
        ToPin = FromPin ? UnrealCodexGraph::FindPin(ToNode, ToPinName, EGPD_Input, ErrorMessage) : nullptr;
    }
    if (!FromPin || (!bAllLinks && !ToPin))
    {
        return UnrealCodexGraph::Error(ErrorMessage);
    }
    if (bAllLinks && FromPin->LinkedTo.IsEmpty())
    {
        return UnrealCodexGraph::Error(TEXT("Pin has no links to disconnect"));
    }
    if (!bAllLinks && !FromPin->LinkedTo.Contains(ToPin))
    {
        return UnrealCodexGraph::Error(TEXT("Specified pins are not connected"));
    }
    const FScopedTransaction Transaction(NSLOCTEXT("UnrealCodexGraph", "DisconnectPins", "Disconnect Blueprint pins"));
    Blueprint->Modify();
    Graph->Modify();
    FromPin->Modify();
    if (bAllLinks)
    {
        const TArray<UEdGraphPin*> LinkedPins = FromPin->LinkedTo;
        for (UEdGraphPin* LinkedPin : LinkedPins)
        {
            if (LinkedPin)
            {
                LinkedPin->Modify();
                GetDefault<UEdGraphSchema_K2>()->BreakPinLinks(*FromPin, true);
            }
        }
    }
    else
    {
        ToPin->Modify();
        GetDefault<UEdGraphSchema_K2>()->BreakSinglePinLink(FromPin, ToPin);
    }
    FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
    const TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetBoolField(TEXT("success"), true);
    Result->SetBoolField(TEXT("all"), bAllLinks);
    return UnrealCodexGraph::ToJson(Result);
}
